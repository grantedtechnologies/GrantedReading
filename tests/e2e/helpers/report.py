"""Structured E2E failure logging: test / input / expected / actual."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _fmt(value: Any, *, limit: int = 500) -> str:
    text = repr(value) if not isinstance(value, str) else value
    text = text.replace("\n", "\\n")
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


@dataclass
class CaseLog:
    """Per-test notebook. Fill before asserting; hook dumps it on failure."""

    test: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    input: Any = None
    expected: Any = None
    actual: Any = None
    notes: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        self.notes.append(message)

    def set(self, *, input: Any = None, expected: Any = None, actual: Any = None) -> None:
        if input is not None:
            self.input = input
        if expected is not None:
            self.expected = expected
        if actual is not None:
            self.actual = actual

    def step(
        self,
        label: str,
        *,
        input: Any = None,
        expected: Any = None,
        actual: Any = None,
    ) -> None:
        entry = {"label": label}
        if input is not None:
            entry["input"] = input
            self.input = input
        if expected is not None:
            entry["expected"] = expected
            self.expected = expected
        if actual is not None:
            entry["actual"] = actual
            self.actual = actual
        self.steps.append(entry)

    def expect(
        self,
        actual: Any,
        expected: Any,
        *,
        input: Any = None,
        label: str | None = None,
    ) -> None:
        """Assert equality and record input / expected / actual for the failure banner."""
        self.set(input=input, expected=expected, actual=actual)
        if label:
            self.step(label, input=input, expected=expected, actual=actual)
        if actual != expected:
            raise AssertionError(
                f"{label or 'assertion'} failed\n"
                f"  test:     {self.test}\n"
                f"  input:    {_fmt(input if input is not None else self.input)}\n"
                f"  expected: {_fmt(expected)}\n"
                f"  actual:   {_fmt(actual)}"
            )

    def expect_contains(
        self,
        haystack: Any,
        needle: Any,
        *,
        input: Any = None,
        label: str | None = None,
    ) -> None:
        text = "" if haystack is None else str(haystack)
        want = "" if needle is None else str(needle)
        self.set(input=input, expected=f"contains {want!r}", actual=text)
        if label:
            self.step(label, input=input, expected=f"contains {want!r}", actual=text)
        if want.lower() not in text.lower():
            raise AssertionError(
                f"{label or 'contains'} failed\n"
                f"  test:     {self.test}\n"
                f"  input:    {_fmt(input if input is not None else self.input)}\n"
                f"  expected: text containing {_fmt(want)}\n"
                f"  actual:   {_fmt(text)}"
            )

    def format_block(self, *, error: str | None = None) -> str:
        lines = [
            "",
            "======== E2E FAILURE ========",
            f"Test:     {self.test}",
        ]
        if self.input is not None:
            lines.append(f"Input:    {_fmt(self.input)}")
        if self.expected is not None:
            lines.append(f"Expected: {_fmt(self.expected)}")
        if self.actual is not None:
            lines.append(f"Actual:   {_fmt(self.actual)}")
        for note in self.notes:
            lines.append(f"Note:     {note}")
        if self.steps:
            lines.append("Steps:")
            for i, step in enumerate(self.steps, 1):
                lines.append(f"  {i}. {step.get('label')}")
                if "input" in step:
                    lines.append(f"     input:    {_fmt(step['input'])}")
                if "expected" in step:
                    lines.append(f"     expected: {_fmt(step['expected'])}")
                if "actual" in step:
                    lines.append(f"     actual:   {_fmt(step['actual'])}")
        if error:
            lines.append(f"Error:    {error}")
        lines.append("=============================")
        lines.append("")
        return "\n".join(lines)
