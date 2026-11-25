"""Small Playwright helpers shared by E2E tests."""

from __future__ import annotations


def wait_for_testid_text(
    page,
    test_id: str,
    *,
    timeout: int = 15000,
    contains: str | None = None,
    clear_first: bool = False,
) -> str:
    """Wait until a data-testid node has non-empty text, then return it.

    Empty status/error divs often have min-height so Playwright's
    `wait_for(state='visible')` returns before the fetch finishes.

    When `contains` is set, wait until the text includes that substring
    (case-insensitive). When `clear_first` is true, blank the node first so
    a prior message cannot satisfy the wait.
    """
    if clear_first:
        page.evaluate(
            """(tid) => {
              const el = document.querySelector(`[data-testid="${tid}"]`);
              if (el) el.textContent = '';
            }""",
            test_id,
        )
    if contains:
        needle = contains.lower()
        page.wait_for_function(
            """([tid, needle]) => {
              const el = document.querySelector(`[data-testid="${tid}"]`);
              return !!(el && (el.textContent || '').toLowerCase().includes(needle));
            }""",
            arg=[test_id, needle],
            timeout=timeout,
        )
    else:
        page.wait_for_function(
            """(tid) => {
              const el = document.querySelector(`[data-testid="${tid}"]`);
              return !!(el && (el.textContent || '').trim().length);
            }""",
            arg=test_id,
            timeout=timeout,
        )
    return page.get_by_test_id(test_id).inner_text().strip()


def clear_testid_text(page, test_id: str) -> None:
    """Blank a status/error node so the next wait cannot match stale text."""
    page.evaluate(
        """(tid) => {
          const el = document.querySelector(`[data-testid="${tid}"]`);
          if (el) el.textContent = '';
        }""",
        test_id,
    )


def open_vocab_section(page) -> None:
    """Expand the Vocab disclosure so add/edit controls are clickable."""
    toggle = page.get_by_test_id("vocab-toggle")
    toggle.wait_for(state="visible", timeout=10000)
    expanded = toggle.get_attribute("aria-expanded")
    if expanded != "true":
        toggle.click()
    page.get_by_test_id("add-vocab-btn").wait_for(state="visible", timeout=10000)
