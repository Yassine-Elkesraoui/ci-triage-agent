"""The D6b tool-using agent: the one-call bundle, then model turns with the toolkit's tools, then a Diagnosis.

The loop is a small LangGraph state graph, used for control flow only (no LangChain model or tool
wrappers): ``model`` (one model turn) -> ``tools`` (run the calls it made, through the case's toolkit) ->
``model`` -> ... -> ``answer`` (parse; repair if invalid). Model calls go through ``ModelCaller.chat``, so
they are paced, capped, retried and recorded like the one-call baseline's.

Turns, as pre-registered in ``configs/agent_v1.toml``:

- the first user turn is exactly the one-call bundle for the mode (``llm_once.gather_bundle`` with the
  config named by ``bundle``);
- turns 1 to max_turns - 1 offer the mode's tools (``Toolkit.tool_definitions``: log-only leaves out
  compare and get_commit, and the toolkit refuses them by name too) and leave the answer free; a turn
  that calls no tool is the answer;
- the last turn, or an earlier one when the conversation plus one more answer would not fit the context
  budget (``context_forced``), offers no tools, adds the prompt's final-turn message and constrains the
  answer to the Diagnosis schema. Before that turn, tool exchanges that still do not fit are dropped,
  newest first: the model never sees those results, recorded with ``shown=False``;
- an answer that is not a valid Diagnosis gets ``repair_calls`` repair calls (schema-constrained, no
  tools); still invalid: invalid_output. A model call that fails after its retries: api_error.

Every tool call goes through the case's toolkit, so its call log holds every output the agent got;
``AgentOutcome.tool_calls`` lists the agent's own calls (after the ``bundle_tool_calls`` the bundle took)
and which were shown. A bad tool name or bad arguments come back as a NotAvailable result, never an error.

Repeated calls: a call with the same name and arguments as one of the agent's earlier calls in the case
(argument key order aside) is not run again; its result is the earlier output, prefixed with the line
``Repeated call: same result as turn N.``. It is recorded with ``repeat_of`` (the earlier call's index in
``tool_calls``) and counted in ``repeated_calls``; the turn that made it counts as a turn as usual. The
bundle's own calls are not the agent's and are never treated as earlier calls.

This package must not import ``ci_triage.evaluation``: ground truth and grading live there.
"""

from __future__ import annotations

import json
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from ci_triage.agent.config import LoadedAgentConfig
from ci_triage.diagnosis import Diagnosis
from ci_triage.llm.calling import APIFailure, LLMCallRecord, ModelCaller
from ci_triage.llm.clients import Message, ToolCallRequest, ToolSpec
from ci_triage.llm.schema import parameters_schema, response_schema
from ci_triage.predictors.base import CaseView
from ci_triage.predictors.llm_once import gather_bundle, parse_diagnosis
from ci_triage.tools import Toolkit, render

Status = Literal["ok", "invalid_output", "api_error"]
Forced = Literal["max_turns", "context"]


class AgentToolCall(BaseModel):
    """One tool call the agent made."""

    turn: int  # the model turn that made it
    name: str
    arguments: Any
    output_chars: int  # of the rendered result
    available: bool  # False: a NotAvailable result (unknown tool, bad arguments, out of scope, ...)
    shown: bool = True  # False: dropped before the model saw it, to fit the context budget
    repeat_of: int | None = None  # index in tool_calls of the earlier identical call; not run again


class TurnRecord(BaseModel):
    turn: int  # 1-based
    tools_offered: bool
    forced: Forced | None = None  # why this was the answer turn, if it was forced
    dropped_tool_calls: int = 0  # tool results dropped before this turn to fit the context budget
    tool_calls: int = 0  # calls the turn made
    prompt_tokens_estimate: int  # system, tool definitions and conversation sent


class AgentOutcome(BaseModel):
    status: Status = "ok"
    diagnosis: Diagnosis | None = None
    error: str | None = None
    repaired: bool = False
    calls: list[LLMCallRecord] = Field(default_factory=list)  # every model call, repair included
    turns: list[TurnRecord] = Field(default_factory=list)
    tool_calls: list[AgentToolCall] = Field(default_factory=list)
    repeated_calls: int = 0  # tool calls identical to an earlier one, answered from it, not run again
    turns_used: int = 0  # model turns, the repair call left out
    context_forced: bool = False  # the answer came early, or results were dropped, for the context budget
    max_turns_forced: bool = False  # the answer turn was the last allowed one
    bundle_tool_calls: int = 0  # toolkit calls the bundle took, before the agent's own
    user_message: str = ""  # the bundle as sent
    diff_included: bool = False
    prompt_chars: int = 0  # system prompt + bundle
    max_prompt_tokens_estimate: int = 0


class AgentError(Exception):
    def __init__(self, outcome: AgentOutcome) -> None:
        super().__init__(f"{outcome.status}: {outcome.error}")
        self.outcome = outcome


def tool_specs(toolkit: Toolkit) -> list[ToolSpec]:
    """The toolkit's tools for its mode, as offered to the model: name, description, flattened parameters."""
    return [ToolSpec(d["name"], d["description"], parameters_schema(d["input_schema"]))
            for d in toolkit.tool_definitions()]


class _State(TypedDict, total=False):
    messages: list[Message]
    turn: int
    pending: list[ToolCallRequest]
    answer: str
    stop: bool


def repeat_prefix(turn: int) -> str:
    return f"Repeated call: same result as turn {turn}."


def call_key(name: Any, arguments: Any) -> str:
    """What makes two tool calls identical: the name and the arguments (JSON objects compare unordered)."""
    return json.dumps([str(name), arguments], sort_keys=True, default=repr)


def _last_exchange(messages: list[Message]) -> int | None:
    """Index of the newest assistant turn that called tools (its results follow it), if any."""
    for i in range(len(messages) - 1, 0, -1):
        if messages[i].role == "assistant" and messages[i].tool_calls:
            return i
    return None


class ToolAgent:
    """The bundle, up to ``max_turns`` model turns with tools, a Diagnosis. ``run`` gives the full outcome;
    ``predict`` (the Predictor protocol) raises ``AgentError`` unless the status is ok."""

    def __init__(self, caller: ModelCaller, loaded: LoadedAgentConfig, name: str | None = None) -> None:
        self.caller = caller
        self.loaded = loaded
        self.name = name or f"{loaded.config.name}-{caller.client.backend}"
        self.schema = response_schema(Diagnosis)

    def _estimate(self, messages: list[Message], tools: list[ToolSpec] | None) -> int:
        text = self.loaded.prompt.system + json.dumps([t.__dict__ for t in tools or []])
        text += "".join(m.content + json.dumps([c.arguments for c in m.tool_calls], default=str) for m in messages)
        return self.loaded.config.context.estimate_tokens(text)

    def run(self, case_view: CaseView, toolkit: Toolkit) -> AgentOutcome:
        config, prompt = self.loaded.config, self.loaded.prompt
        one_call = self.loaded.bundle
        bundle = gather_bundle(case_view, toolkit, one_call.config.budget, len(one_call.prompt.system),
                               one_call.config.generation.max_output_tokens)
        user = bundle.user_message()
        outcome = AgentOutcome(user_message=user, diff_included=bundle.diff_included,
                               bundle_tool_calls=len(toolkit.calls), prompt_chars=len(prompt.system) + len(user))
        specs = tool_specs(toolkit)
        earlier: dict[str, tuple[int, str]] = {}  # call key -> (index in tool_calls, rendered output)
        room = config.context.room(config.generation.max_output_tokens)

        def model_turn(state: _State) -> _State:
            turn, messages = state["turn"] + 1, list(state["messages"])
            forced: Forced | None = None
            if turn >= config.agent.max_turns:
                forced = "max_turns"
            elif self._estimate(messages, specs) > room:
                forced = "context"
            dropped = 0
            if forced:
                final = Message("user", prompt.final_turn or "")
                while self._estimate([*messages, final], None) > room and (cut := _last_exchange(messages)):
                    dropped += sum(m.role == "tool" for m in messages[cut:])
                    messages = messages[:cut]
                shown = [c for c in outcome.tool_calls if c.shown]
                for call in shown[len(shown) - dropped:]:
                    call.shown = False
                messages.append(final)
                outcome.max_turns_forced = forced == "max_turns"
                outcome.context_forced = forced == "context" or dropped > 0
            tools = None if forced else specs
            estimate = self._estimate(messages, tools)
            outcome.max_prompt_tokens_estimate = max(outcome.max_prompt_tokens_estimate, estimate)
            record = TurnRecord(turn=turn, tools_offered=tools is not None, forced=forced, dropped_tool_calls=dropped,
                                prompt_tokens_estimate=estimate)
            outcome.turns.append(record)
            outcome.turns_used = turn
            try:
                response, call = self.caller.chat("answer" if forced else "turn", prompt.system, messages,
                                                  tools=tools, schema=self.schema if forced else None)
            except APIFailure as failure:
                outcome.calls.append(failure.record)
                outcome.status, outcome.error = "api_error", f"api_error: {failure}"
                return {"turn": turn, "stop": True}
            outcome.calls.append(call)
            if tools is not None and response.tool_calls:
                record.tool_calls = len(response.tool_calls)
                messages.append(Message("assistant", response.text, tool_calls=response.tool_calls))
                return {"messages": messages, "turn": turn, "pending": list(response.tool_calls)}
            return {"messages": messages, "turn": turn, "pending": [], "answer": response.text}

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

        def answer(state: _State) -> _State:
            text = state.get("answer", "")
            diagnosis, error = parse_diagnosis(text)
            messages = list(state["messages"])
            for _ in range(config.generation.repair_calls):
                if diagnosis is not None:
                    break
                messages += [Message("assistant", text or "(empty answer)"),
                             Message("user", prompt.repair_message(error or "invalid"))]
                try:
                    response, call = self.caller.chat("repair", prompt.system, messages, tools=None,
                                                      schema=self.schema)
                except APIFailure as failure:
                    outcome.calls.append(failure.record)
                    outcome.status, outcome.error = "api_error", f"api_error: {failure}"
                    return {"stop": True}
                outcome.calls.append(call)
                text = response.text
                diagnosis, error = parse_diagnosis(text)
                outcome.repaired = diagnosis is not None
            if diagnosis is None:
                outcome.status, outcome.error = "invalid_output", f"invalid_output: {error}"
            else:
                outcome.diagnosis = diagnosis
            return {"stop": True}

        def after_model(state: _State) -> str:
            if state.get("stop"):
                return END
            return "tools" if state.get("pending") else "answer"

        graph = StateGraph(_State)
        graph.add_node("model", model_turn)
        graph.add_node("tools", run_tools)
        graph.add_node("answer", answer)
        graph.add_edge(START, "model")
        graph.add_conditional_edges("model", after_model, {"tools": "tools", "answer": "answer", END: END})
        graph.add_edge("tools", "model")
        graph.add_edge("answer", END)
        graph.compile().invoke({"messages": [Message("user", user)], "turn": 0, "pending": []},
                               config={"recursion_limit": 2 * config.agent.max_turns + 4})
        return outcome

    def predict(self, case_view: CaseView, toolkit: Toolkit) -> Diagnosis:
        outcome = self.run(case_view, toolkit)
        if outcome.diagnosis is None:
            raise AgentError(outcome)
        return outcome.diagnosis
