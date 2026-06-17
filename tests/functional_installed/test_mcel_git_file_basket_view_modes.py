from __future__ import annotations

import pytest


pytestmark = pytest.mark.installed_functional


LAB_SELECTOR = "[data-mcel-git-file-basket-treegrid-lab]"
VIEW_MODE_BUTTON_SELECTOR = "[data-mcel-git-treegrid-view-mode-option]"
PROJECTION_SELECTOR = "[data-mcel-git-treegrid-view-projection-mount]"
PROJECTION_SELECTION_SELECTOR = "input[data-mcel-git-view-selection-path]"
TREEGRID_SELECTION_SELECTOR = "input[data-git-commit-contract-checkbox]"
ANY_SELECTION_SELECTOR = f"{PROJECTION_SELECTION_SELECTOR}, {TREEGRID_SELECTION_SELECTOR}"
ANY_ENABLED_SELECTION_SELECTOR = (
    "input[data-mcel-git-view-selection-path]:not([disabled]), "
    "input[data-git-commit-contract-checkbox]:not([disabled])"
)
ANY_DISABLED_SELECTION_SELECTOR = (
    "input[data-mcel-git-view-selection-path][disabled], "
    "input[data-git-commit-contract-checkbox][disabled]"
)


def _expect(page):
    try:
        from playwright.sync_api import expect
    except Exception as exc:  # pragma: no cover - guarded by fixture
        pytest.skip(f"Playwright expect API is required: {exc}")
    return expect


def _open_mcel_git_file_basket_views(page, viewport_app):
    expect = _expect(page)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{viewport_app.base_url}/applications/mcel-lab", wait_until="domcontentloaded")
    page.wait_for_function("() => document.body.dataset.activeApp === 'mcel-lab'")
    expect(page.locator("[data-mcel-element-acid-root]")).to_be_visible(timeout=15_000)
    page.locator("[data-mcel-lab-mode='views']").click()
    page.wait_for_function(
        """() => {
            const root = document.querySelector("[data-mcel-element-acid-root]");
            return root?.querySelector("[data-mcel-lab-panel='views']:not([hidden])");
        }"""
    )
    lab = page.locator(LAB_SELECTOR).nth(0)
    expect(lab).to_be_visible(timeout=15_000)
    return lab


def _view_mode_ids(page) -> list[str]:
    mode_ids = page.locator(f"{LAB_SELECTOR} {VIEW_MODE_BUTTON_SELECTOR}").evaluate_all(
        """(buttons) => buttons
          .map((button) => button.getAttribute("data-mcel-git-treegrid-view-mode-option"))
          .filter(Boolean)
        """
    )
    return [str(mode_id) for mode_id in mode_ids]


def _activate_view_mode(page, lab, mode_id: str) -> None:
    lab.locator(f"{VIEW_MODE_BUTTON_SELECTOR}[data-mcel-git-treegrid-view-mode-option='{mode_id}']").click()
    page.wait_for_function(
        """([labSelector, expectedMode]) => {
            const lab = document.querySelector(labSelector);
            return lab?.getAttribute("data-mcel-git-treegrid-view-mode") === expectedMode &&
              lab.querySelector("[data-mcel-git-treegrid-view-projection-mount]");
        }""",
        arg=[LAB_SELECTOR, mode_id],
    )


def _clear_selection(page, lab) -> None:
    lab.locator("[data-mcel-git-treegrid-command='clear']").click()
    page.wait_for_function(
        """(labSelector) => {
            const lab = document.querySelector(labSelector);
            return lab?.getAttribute("data-mcel-git-treegrid-selected-count") === "0" ||
              lab?.querySelector("[data-mcel-git-treegrid-selected-output]")?.textContent?.includes("No selectable files selected.");
        }""",
        arg=LAB_SELECTOR,
    )


def _exercise_first_selection_control(page) -> dict:
    """Click one enabled control through the browser DOM and report scroll stability.

    Some rejected/projection views rebuild their whole projection on every selection
    change. This catches the Thunar-style regression where a selection event causes
    the active view surface, or the page itself, to snap back to the top.
    """

    return page.evaluate(
        """({labSelector, selectionSelector, disabledSelector}) => {
            const lab = document.querySelector(labSelector);
            if (!lab) throw new Error(`Missing lab surface: ${labSelector}`);

            const projection = lab.querySelector("[data-mcel-git-treegrid-view-projection-mount]");
            const allControls = Array.from(lab.querySelectorAll(selectionSelector));
            const disabledControls = Array.from(lab.querySelectorAll(disabledSelector));
            const enabledControls = allControls.filter((input) => !input.disabled);
            if (!enabledControls.length) {
              return {
                ok: false,
                reason: "no enabled selection controls",
                selectionControlCount: allControls.length,
                disabledControlCount: disabledControls.length,
                selectedOutputText: lab.querySelector("[data-mcel-git-treegrid-selected-output]")?.textContent || ""
              };
            }

            function firstScrollableNode() {
              const candidates = Array.from(lab.querySelectorAll([
                "[data-mcel-git-view-scroll-surface]",
                ".mcel-git-view-compact-audit",
                ".mcel-git-view-flat-table",
                ".mcel-git-view-column-browser",
                ".mcel-git-view-icon-grid",
                ".mcel-git-view-split-details",
                ".mcel-git-view-title-tree",
                ".git-project-contract-treegrid-body",
                ".git-project-contract-treegrid",
                "[data-mcel-git-treegrid-view-projection-mount]"
              ].join(",")));
              return candidates.find((node) => node.scrollHeight > node.clientHeight + 4) ||
                projection ||
                lab;
            }

            const surface = firstScrollableNode();
            const maxSurfaceScroll = Math.max(0, surface.scrollHeight - surface.clientHeight);
            const desiredSurfaceScroll = Math.min(96, maxSurfaceScroll);
            surface.scrollTop = desiredSurfaceScroll;

            const maxWindowScroll = Math.max(
              0,
              document.documentElement.scrollHeight - window.innerHeight
            );
            const labTop = lab.getBoundingClientRect().top + window.scrollY;
            const desiredWindowScroll = Math.min(maxWindowScroll, Math.max(0, labTop + 120));
            window.scrollTo(0, desiredWindowScroll);

            const beforeSurfaceScroll = surface.scrollTop;
            const beforeWindowScroll = window.scrollY;
            function selectionKind(input) {
              return input.getAttribute("data-mcel-git-view-selection-kind") ||
                input.getAttribute("data-git-commit-contract-checkbox") ||
                "";
            }

            const target = enabledControls.find((input) => selectionKind(input) === "file" && !input.checked) ||
              enabledControls.find((input) => selectionKind(input) === "file") ||
              enabledControls.find((input) => !input.checked) ||
              enabledControls[0];
            const path = target.getAttribute("data-mcel-git-view-selection-path") ||
              target.getAttribute("data-git-commit-contract-path") ||
              "";
            const kind = selectionKind(target);

            target.click();

            const nextProjection = lab.querySelector("[data-mcel-git-treegrid-view-projection-mount]");
            const nextSurface = firstScrollableNode();
            const afterSurfaceScroll = nextSurface.scrollTop;
            const afterWindowScroll = window.scrollY;
            const selectedOutputText = lab.querySelector("[data-mcel-git-treegrid-selected-output]")?.textContent || "";

            return {
              ok: true,
              path,
              kind,
              projectionWasReplaced: projection !== nextProjection,
              selectionControlCount: allControls.length,
              disabledControlCount: disabledControls.length,
              beforeSurfaceScroll,
              afterSurfaceScroll,
              beforeWindowScroll,
              afterWindowScroll,
              selectedOutputText
            };
        }""",
        {
            "labSelector": LAB_SELECTOR,
            "selectionSelector": ANY_SELECTION_SELECTOR,
            "disabledSelector": ANY_DISABLED_SELECTION_SELECTOR,
        },
    )


def test_mcel_git_file_basket_every_view_mode_exposes_selection_and_preserves_scroll(
    playwright_page,
    viewport_app,
):
    page = playwright_page
    lab = _open_mcel_git_file_basket_views(page, viewport_app)

    mode_ids = _view_mode_ids(page)
    assert mode_ids, "MCEL Git file-basket lab exposed no view modes"
    assert len(mode_ids) >= 8, f"Expected the Git specimen to expose the view-mode catalog, got {mode_ids!r}"

    failures: list[str] = []
    for mode_id in mode_ids:
        _activate_view_mode(page, lab, mode_id)
        lab.locator(PROJECTION_SELECTOR).wait_for(state="visible")
        _clear_selection(page, lab)

        selection_controls = lab.locator(ANY_SELECTION_SELECTOR).count()
        enabled_controls = lab.locator(ANY_ENABLED_SELECTION_SELECTOR).count()
        disabled_controls = lab.locator(ANY_DISABLED_SELECTION_SELECTOR).count()
        if selection_controls <= 0:
            failures.append(f"{mode_id}: no user selection controls were rendered")
            continue
        if enabled_controls <= 0:
            failures.append(f"{mode_id}: selection controls rendered, but none are enabled")
            continue
        if disabled_controls <= 0:
            failures.append(f"{mode_id}: no disabled control proves blocked rows are visible/non-selectable")

        result = _exercise_first_selection_control(page)
        if not result.get("ok"):
            failures.append(f"{mode_id}: {result.get('reason')}")
            continue

        selected_output = str(result.get("selectedOutputText") or "")
        selected_path = str(result.get("path") or "")
        selected_kind = str(result.get("kind") or "")
        if selected_kind == "file" and selected_path and selected_path not in selected_output:
            failures.append(
                f"{mode_id}: selecting file {selected_path!r} did not add that explicit repo-relative path "
                f"to the selected output ({selected_output!r})"
            )
        elif selected_kind != "file" and "No selectable files selected." in selected_output:
            failures.append(
                f"{mode_id}: selecting {selected_kind or 'unknown'} control {selected_path!r} left selected output empty"
            )

        surface_delta = abs(int(result.get("afterSurfaceScroll") or 0) - int(result.get("beforeSurfaceScroll") or 0))
        window_delta = abs(int(result.get("afterWindowScroll") or 0) - int(result.get("beforeWindowScroll") or 0))
        if surface_delta > 4:
            failures.append(
                f"{mode_id}: selection changed active surface scrollTop from "
                f"{result.get('beforeSurfaceScroll')} to {result.get('afterSurfaceScroll')}"
            )
        if window_delta > 4:
            failures.append(
                f"{mode_id}: selection changed page scrollY from "
                f"{result.get('beforeWindowScroll')} to {result.get('afterWindowScroll')}"
            )

    assert not failures, "Git file-basket view-mode interaction failures:\n" + "\n".join(failures)


def test_mcel_git_file_basket_treegrid_modes_exercise_disclosure_and_column_resize(
    playwright_page,
    viewport_app,
):
    page = playwright_page
    lab = _open_mcel_git_file_basket_views(page, viewport_app)

    mode_ids = set(_view_mode_ids(page))
    tree_modes = [mode_id for mode_id in ["contract-treegrid", "details-tree", "details-treegrid"] if mode_id in mode_ids]
    assert tree_modes, f"No native treegrid-capable modes were exposed; available modes: {sorted(mode_ids)!r}"

    failures: list[str] = []
    for mode_id in tree_modes:
        _activate_view_mode(page, lab, mode_id)

        disclosure = lab.locator("[data-git-commit-contract-disclosure]").nth(0)
        disclosure.wait_for(state="visible")

        before_visible_rows = lab.locator(
            "[data-git-commit-contract-row][data-git-commit-contract-visible='true']"
        ).count()
        disclosure.click()
        page.wait_for_timeout(100)
        after_visible_rows = lab.locator(
            "[data-git-commit-contract-row][data-git-commit-contract-visible='true']"
        ).count()
        if after_visible_rows == before_visible_rows:
            failures.append(
                f"{mode_id}: disclosure click did not change visible row count "
                f"({before_visible_rows} before, {after_visible_rows} after)"
            )

        # Restore expansion before testing the resize handle, so the tree remains usable for diagnosis.
        disclosure.click()
        page.wait_for_timeout(100)

        handle = lab.locator("[data-git-commit-contract-resize-handle]").nth(0)
        if handle.count() <= 0:
            failures.append(f"{mode_id}: no column resize handles were rendered")
            continue
        handle.wait_for(state="visible")

        treegrid = lab.locator("[data-git-commit-contract-treegrid]").nth(0)
        before_width = treegrid.evaluate(
            """(node) => getComputedStyle(node).getPropertyValue("--git-treegrid-path-col")"""
        )
        box = handle.bounding_box()
        if not box:
            failures.append(f"{mode_id}: resize handle had no bounding box")
            continue

        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] / 2 + 48, box["y"] + box["height"] / 2)
        page.mouse.up()

        page.wait_for_function(
            """(labSelector) => {
                const treegrid = document.querySelector(`${labSelector} [data-git-commit-contract-treegrid]`);
                return treegrid?.dataset?.gitCommitContractColumnResized === "true";
            }""",
            arg=LAB_SELECTOR,
        )
        after_width = treegrid.evaluate(
            """(node) => getComputedStyle(node).getPropertyValue("--git-treegrid-path-col")"""
        )
        if after_width == before_width:
            failures.append(f"{mode_id}: dragging the resize handle did not change the path column width")

    assert not failures, "Git file-basket native treegrid failures:\n" + "\n".join(failures)
