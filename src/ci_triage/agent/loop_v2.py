"""Agent v2 (phase B, pre-registered in docs/preregistration/phase-b.md, section 2, changes a to e): the v1 loop
(``loop.py``, unchanged) with these differences, as ``configs/agent_v2.toml`` sets them.

a. **Answer through a tool.** The answer is a call to ``submit_diagnosis``, whose parameters are the Diagnosis
   JSON schema (``response_schema(Diagnosis)``, the schema v1's answer turn uses). Turns 1 to max_turns - 1
   offer the mode's tools plus ``submit_diagnosis``. The answer turn offers ``submit_diagnosis`` only, as a
   forced call, where the client can force one (``can_force_tool_call``: Gemini); elsewhere (Ollama) it offers
   no tools and constrains the answer to the schema, as v1's answer turn did. A turn that submits ends the case;
   its other tool calls are not run. The case gets one repair call, schema- or tool-forced like the answer
   turn, in two situations: a turn with neither a tool call nor a submission (answered with the repair message,
   asking for a submission), or a submission that fails validation (the errors go back as the tool's result).
   Still no valid diagnosis: invalid_output.
b. **One sentence beside the definitions**, in ``prompts/agent_v2.md`` only.
c. **Repeated calls end the search.** A repeated call (same tool, same arguments as an earlier agent call in
   the case) is answered from the earlier result, as in v1. Once the case has ``repeated_calls_to_answer``
   repeated calls, the next model turn is the answer turn, recorded as ``forced="repeat"`` (``repeat_forced``).
   When several reasons apply, ``repeat`` wins over ``max_turns``, which wins over ``context``.
d. **Unparseable tool calls.** The caller does not retry an error that ``is_unparseable_tool_call`` matches.
   The first in a case uses up its turn and is shown to the model as the prompt's tool-call error message (a
   user turn) before the next turn; on the answer turn, where no turn follows, it goes into the repair call's
   errors instead. A second in the same case: invalid_output. Other errors keep v1's retry rules; an error that
   still fails after them is api_error.
e. **Context estimate.** Characters per token come per backend from the config. Once a model call has
   returned the server's prompt count (``tokens_in``), the check for a conversation that extends the one that
   call sent uses that count plus the estimate for the new messages only (and for any tool definitions added);
   otherwise (the first turn, or after results were dropped) it estimates everything, as v1 does.

Everything else is v1: the first user turn is exactly the one-call bundle, max_turns, the context forcing and
dropping of tool results, the records of tool calls and repeats, and the graph (LangGraph, control flow only).

This module must not import ``ci_triage.evaluation``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ValidationError

from ci_triage.agent.config import LoadedAgentConfig
from ci_triage.agent.loop import (AgentError, AgentToolCall, Status, ToolAgent, _last_exchange, call_key,
                                 repeat_prefix, tool_specs)
from ci_triage.diagnosis import Diagnosis
from ci_triage.llm.calling import APIFailure, LLMCallRecord, ModelCaller, scrub
from ci_triage.llm.clients import APICallError, LLMResponse, Message, ToolCallRequest, ToolSpec
from ci_triage.llm.schema import response_schema
from ci_triage.predictors.base import CaseView
from ci_triage.predictors.llm_once import gather_bundle, parse_diagnosis
from ci_triage.tools import NotAvailableResult, Toolkit, render

SUBMIT = "submit_diagnosis"
SUBMIT_DESCRIPTION = "Submit the diagnosis of the failed run. Calling it ends the case."
NO_SUBMISSION = "the reply called no tool and submitted no diagnosis"
NOT_RUN = "not run: this turn submitted a diagnosis"

# d. The server's error text when it could not parse the model's tool call. Ollama 0.40.16 (D6b, inj-024)
# answered HTTP 500 with "XML syntax error on line 4: element <function> closed by </parameter>" from its
# qwen3.5 tool-call parser (logged as "qwen3.5 tool call parsing failed"); Ollama's generic tool parser says
# "error parsing tool call". Matched on errors the server returned (a status is set), case-insensitively.
UNPARSEABLE_TOOL_CALL = re.compile(r"XML syntax error|error parsing tool call|tool call parsing failed", re.IGNORECASE)

Forced = Literal["max_turns", "context", "repeat"]
AnsweredBy = Literal["submit_diagnosis", "schema"]


def make_agent(caller: ModelCaller, loaded: LoadedAgentConfig, name: str | None = None) -> ToolAgent | ToolAgentV2:
    """The agent the config's ``[agent] loop`` names: v1 (``loop.ToolAgent``, the default) or v2."""
    return (ToolAgentV2 if loaded.config.agent.loop == "v2" else ToolAgent)(caller, loaded, name)


def no_retry_rule(loaded: LoadedAgentConfig):
    """The caller's ``no_retry`` rule for the config's loop: d's for v2, none for v1."""
    return is_unparseable_tool_call if loaded.config.agent.loop == "v2" else None


def is_unparseable_tool_call(error: APICallError) -> bool:
    """d: the server says it could not parse the model's tool call."""
    return error.status is not None and bool(UNPARSEABLE_TOOL_CALL.search(error.message or ""))


def submit_spec() -> ToolSpec:
    return ToolSpec(SUBMIT, SUBMIT_DESCRIPTION, response_schema(Diagnosis))


def validate_submission(arguments: Any) -> tuple[Diagnosis | None, str | None]:
    """The submitted diagnosis, or None and what is wrong with it (worded like ``parse_diagnosis``)."""
    if not isinstance(arguments, dict):
        return None, "answer: the arguments are not a JSON object"
    try:
        return Diagnosis.model_validate(arguments), None
    except ValidationError as exc:
        errors = [f"{'.'.join(map(str, e['loc'])) or 'answer'}: {e['msg']}" for e in exc.errors()[:8]]
        more = f" (+{len(exc.errors()) - 8} more)" if len(exc.errors()) > 8 else ""
        return None, ("; ".join(errors) + more)[:1000]


class TurnRecordV2(BaseModel):
    turn: int  # 1-based
    tools_offered: bool  # the mode's tools (with submit_diagnosis)
    submit_offered: bool  # submit_diagnosis offered (forced on the answer turn when the client can force it)
    forced: Forced | None = None  # why this was the answer turn, if it was forced
    dropped_tool_calls: int = 0
    tool_calls: int = 0  # calls the turn made, submit_diagnosis included
    submitted: bool = False  # the turn called submit_diagnosis
    unparseable_tool_call: str | None = None  # d: the server's error, when it could not parse the turn's tool call
    prompt_tokens_estimate: int  # what the context check used for this turn's prompt
    server_count_used: bool = False  # e: the estimate started from the server's count of an earlier prompt


class AgentOutcomeV2(BaseModel):
    status: Status = "ok"
    diagnosis: Diagnosis | None = None
    answered_by: AnsweredBy | None = None  # a submit_diagnosis call, or the schema-constrained answer
    error: str | None = None
    repaired: bool = False
    calls: list[LLMCallRecord] = Field(default_factory=list)
    turns: list[TurnRecordV2] = Field(default_factory=list)
    tool_calls: list[AgentToolCall] = Field(default_factory=list)  # tools run (or repeated), not submissions
    repeated_calls: int = 0
    turns_used: int = 0
    context_forced: bool = False
    max_turns_forced: bool = False
    repeat_forced: bool = False
    unparseable_tool_calls: int = 0
    bundle_tool_calls: int = 0
    user_message: str = ""
    diff_included: bool = False
    prompt_chars: int = 0
    max_prompt_tokens_estimate: int = 0


class _State(TypedDict, total=False):
    messages: list[Message]
    turn: int
    pending: list[ToolCallRequest]
    repair: bool
    stop: bool


def _chars(messages: list[Message]) -> int:
    """Characters of ``messages`` as the estimate counts them (v1's ``ToolAgent._estimate``)."""
    return sum(len(m.content) + len(json.dumps([c.arguments for c in m.tool_calls], default=str)) for m in messages)


def _tools_chars(tools: list[ToolSpec] | None) -> int:
    return len(json.dumps([t.__dict__ for t in tools or []]))


class ToolAgentV2:
    """Agent v2. ``run`` gives the full outcome; ``predict`` raises ``AgentError`` unless the status is ok."""

    def __init__(self, caller: ModelCaller, loaded: LoadedAgentConfig, name: str | None = None) -> None:
        if loaded.config.agent.loop != "v2":
            raise ValueError(f"{loaded.path.name} is not a v2 agent config")
        self.caller = caller
        self.loaded = loaded
        self.name = name or f"{loaded.config.name}-{caller.client.backend}"
        self.schema = response_schema(Diagnosis)

    def run(self, case_view: CaseView, toolkit: Toolkit) -> AgentOutcomeV2:
        config, prompt = self.loaded.config, self.loaded.prompt
        one_call = self.loaded.bundle
        bundle = gather_bundle(case_view, toolkit, one_call.config.budget, len(one_call.prompt.system),
                               one_call.config.generation.max_output_tokens)
        user = bundle.user_message()
        outcome = AgentOutcomeV2(user_message=user, diff_included=bundle.diff_included,
                                 bundle_tool_calls=len(toolkit.calls), prompt_chars=len(prompt.system) + len(user))
        client = self.caller.client
        backend, can_force = client.backend, bool(getattr(client, "can_force_tool_call", False))
        submit = submit_spec()
        offered = [*tool_specs(toolkit), submit]
        answer_tools = [submit] if can_force else None
        earlier: dict[str, tuple[int, str]] = {}
        room = config.context.room(config.generation.max_output_tokens)
        context = config.context
        last: dict[str, Any] = {"count": None, "messages": [], "tools_chars": 0}  # e: the last server count

        def estimate(messages: list[Message], tools: list[ToolSpec] | None) -> tuple[int, bool]:
            tools_chars = _tools_chars(tools)
            sent = last["messages"]
            if (context.use_server_count and last["count"] is not None and len(messages) >= len(sent)
                    and messages[:len(sent)] == sent):
                new = _chars(messages[len(sent):]) + max(0, tools_chars - last["tools_chars"])
                return last["count"] + context.tokens_for(new, backend), True
            return context.tokens_for(len(prompt.system) + tools_chars + _chars(messages), backend), False

        def chat(purpose: str, messages: list[Message], tools: list[ToolSpec] | None,
                 forced_answer: bool) -> tuple[LLMResponse, LLMCallRecord]:
            """One model call; on an answer or repair call, submit_diagnosis forced or the schema."""
            schema = self.schema if forced_answer and not can_force else None
            force_tool = SUBMIT if forced_answer and can_force else None
            response, call = self.caller.chat(purpose, prompt.system, messages, tools=tools, schema=schema,
                                              force_tool=force_tool)
            if response.tokens_in is not None:
                last.update(count=response.tokens_in, messages=list(messages), tools_chars=_tools_chars(tools))
            return response, call

        def finish(diagnosis: Diagnosis | None, error: str | None, answered_by: AnsweredBy) -> _State:
            if diagnosis is None:
                outcome.status, outcome.error = "invalid_output", f"invalid_output: {error}"
            else:
                outcome.diagnosis, outcome.answered_by = diagnosis, answered_by
            return {"stop": True}

        def api_error(failure: APIFailure) -> _State:
            outcome.calls.append(failure.record)
            outcome.status, outcome.error = "api_error", f"api_error: {failure}"
            return {"stop": True}

        def unparseable(failure: APIFailure) -> str:
            outcome.calls.append(failure.record)
            outcome.unparseable_tool_calls += 1
            message = failure.error.message if failure.error is not None else str(failure)
            return scrub(message, self.caller.secrets) or ""

        def to_repair(messages: list[Message], response: LLMResponse, errors: str, submission: bool) -> _State:
            """Messages for the repair call: the reply, then the errors as the submission's result, or as the
            repair message after a reply with no submission."""
            messages = list(messages)
            if submission:
                messages.append(Message("assistant", response.text, tool_calls=response.tool_calls))
                for request in response.tool_calls:
                    content = prompt.repair_message(errors) if request.name == SUBMIT else \
                        render(NotAvailableResult.of(f"{str(request.name)[:200]} call", NOT_RUN))
                    messages.append(Message("tool", content, tool_name=str(request.name)[:200],
                                            tool_call_id=request.id))
            else:
                messages += [Message("assistant", response.text or "(empty answer)"),
                             Message("user", prompt.repair_message(errors))]
            return {"messages": messages, "pending": [], "repair": True}

        def take_answer(response: LLMResponse, schema_answer: bool) -> tuple[Diagnosis | None, str | None,
                                                                             AnsweredBy, bool]:
            """(diagnosis, errors, how, whether a submission was made) from a reply."""
            submissions = [c for c in response.tool_calls if c.name == SUBMIT]
            if submissions:
                diagnosis, error = validate_submission(submissions[0].arguments)
                return diagnosis, error, "submit_diagnosis", True
            if schema_answer:
                diagnosis, error = parse_diagnosis(response.text)
                return diagnosis, error, "schema", False
            return None, NO_SUBMISSION, "submit_diagnosis", False

        def model_turn(state: _State) -> _State:
            turn, messages = state["turn"] + 1, list(state["messages"])
            forced: Forced | None = None
            if outcome.repeated_calls >= (config.agent.repeated_calls_to_answer or 0):
                forced = "repeat"
            elif turn >= config.agent.max_turns:
                forced = "max_turns"
            elif estimate(messages, offered)[0] > room:
                forced = "context"
            dropped = 0
            if forced:
                final = Message("user", prompt.final_turn or "")
                while estimate([*messages, final], answer_tools)[0] > room and (cut := _last_exchange(messages)):
                    dropped += sum(m.role == "tool" for m in messages[cut:])
                    messages = messages[:cut]
                shown = [c for c in outcome.tool_calls if c.shown]
                for call in shown[len(shown) - dropped:]:
                    call.shown = False
                messages.append(final)
                outcome.repeat_forced = forced == "repeat"
                outcome.max_turns_forced = forced == "max_turns"
                outcome.context_forced = forced == "context" or dropped > 0
            tools = answer_tools if forced else offered
            tokens, from_server = estimate(messages, tools)
            outcome.max_prompt_tokens_estimate = max(outcome.max_prompt_tokens_estimate, tokens)
            record = TurnRecordV2(turn=turn, tools_offered=not forced, submit_offered=tools is not None,
                                  forced=forced, dropped_tool_calls=dropped, prompt_tokens_estimate=tokens,
                                  server_count_used=from_server)
            outcome.turns.append(record)
            outcome.turns_used = turn
            try:
                response, call = chat("answer" if forced else "turn", messages, tools, forced is not None)
            except APIFailure as failure:
                if not failure.not_retried:
                    return api_error(failure)
                error = unparseable(failure)
                record.unparseable_tool_call = error
                if outcome.unparseable_tool_calls > 1:
                    return finish(None, f"the server could not parse a tool call twice ({error})", "submit_diagnosis")
                if forced:  # no turn follows: the error goes to the repair call
                    return to_repair(messages, LLMResponse("", None, None, None, 0.0), f"the server could not "
                                     f"parse the reply's tool call ({error})", False)
                messages.append(Message("user", prompt.tool_call_error_message(error)))
                return {"messages": messages, "turn": turn, "pending": []}
            outcome.calls.append(call)
            record.tool_calls = len(response.tool_calls) if tools is not None else 0
            record.submitted = tools is not None and any(c.name == SUBMIT for c in response.tool_calls)
            if not forced and response.tool_calls and not record.submitted:
                messages.append(Message("assistant", response.text, tool_calls=response.tool_calls))
                return {"messages": messages, "turn": turn, "pending": list(response.tool_calls)}
            diagnosis, error, how, submission = take_answer(
                response if tools is not None else LLMResponse(response.text, None, None, None, 0.0),
                schema_answer=forced is not None and not can_force)
            if diagnosis is not None:
                return finish(diagnosis, None, how)
            return {**to_repair(messages, response, error or "invalid", submission), "turn": turn}

        def run_tools(state: _State) -> _State:
            messages = list(state["messages"])
            for request in state["pending"]:
                key, name = call_key(request.name, request.arguments), str(request.name)[:200]
                if key in earlier:
                    index, output = earlier[key]
                    first = outcome.tool_calls[index]
                    content = f"{repeat_prefix(first.turn)}\n{output}"
                    messages.append(Message("tool", content, tool_name=name, tool_call_id=request.id))
                    outcome.tool_calls.append(AgentToolCall(turn=state["turn"], name=name, arguments=first.arguments,
                                                            output_chars=len(content), available=first.available,
                                                            repeat_of=index))
                    outcome.repeated_calls += 1
                    continue
                output = render(toolkit.call(request.name, request.arguments))
                logged = toolkit.calls[-1]
                earlier[key] = (len(outcome.tool_calls), output)
                messages.append(Message("tool", output, tool_name=name, tool_call_id=request.id))
                outcome.tool_calls.append(AgentToolCall(turn=state["turn"], name=name, arguments=logged.arguments,
                                                        output_chars=logged.output_chars, available=logged.available))
            return {"messages": messages, "pending": []}

        def repair(state: _State) -> _State:
            messages = list(state["messages"])
            try:
                response, call = chat("repair", messages, answer_tools, True)
            except APIFailure as failure:
                if not failure.not_retried:
                    return api_error(failure)
                error = unparseable(failure)
                return finish(None, f"the server could not parse the repair's tool call ({error})", "submit_diagnosis")
            outcome.calls.append(call)
            diagnosis, error, how, _ = take_answer(response, schema_answer=not can_force)
            outcome.repaired = diagnosis is not None
            return finish(diagnosis, error, how)

        def after_model(state: _State) -> str:
            if state.get("stop"):
                return END
            if state.get("repair"):
                return "repair"
            return "tools" if state.get("pending") else "model"

        graph = StateGraph(_State)
        graph.add_node("model", model_turn)
        graph.add_node("tools", run_tools)
        graph.add_node("repair", repair)
        graph.add_edge(START, "model")
        graph.add_conditional_edges("model", after_model, {"tools": "tools", "model": "model", "repair": "repair",
                                                           END: END})
        graph.add_edge("tools", "model")
        graph.add_edge("repair", END)
        graph.compile().invoke({"messages": [Message("user", user)], "turn": 0, "pending": []},
                               config={"recursion_limit": 2 * config.agent.max_turns + 6})
        return outcome

    def predict(self, case_view: CaseView, toolkit: Toolkit) -> Diagnosis:
        outcome = self.run(case_view, toolkit)
        if outcome.diagnosis is None:
            raise AgentError(outcome)  # type: ignore[arg-type]
        return outcome.diagnosis
