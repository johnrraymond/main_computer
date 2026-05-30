    var McelLabBrowserObserver = (() => {
      const contract = typeof McelLabContract !== "undefined" ? McelLabContract : window.McelLabContract;
      const {attributes} = contract;

      function safeNumber(value) {
        const number = Number(value);
        return Number.isFinite(number) ? number : 0;
      }

      function rectFor(element) {
        if (typeof element?.getBoundingClientRect === "function") {
          const rect = element.getBoundingClientRect();
          return {
            x: safeNumber(rect.x),
            y: safeNumber(rect.y),
            width: safeNumber(rect.width),
            height: safeNumber(rect.height),
            top: safeNumber(rect.top),
            right: safeNumber(rect.right),
            bottom: safeNumber(rect.bottom),
            left: safeNumber(rect.left)
          };
        }
        return {x: 0, y: 0, width: 0, height: 0, top: 0, right: 0, bottom: 0, left: 0};
      }

      function computedStyleFor(element) {
        const view = element?.ownerDocument?.defaultView || (typeof window !== "undefined" ? window : null);
        const style = view?.getComputedStyle ? view.getComputedStyle(element) : null;
        return {
          overflow: style?.overflow || element?.style?.overflow || "",
          overflowX: style?.overflowX || element?.style?.overflowX || "",
          overflowY: style?.overflowY || element?.style?.overflowY || "",
          display: style?.display || element?.style?.display || "",
          position: style?.position || element?.style?.position || "",
          contain: style?.contain || element?.style?.contain || "",
          maxHeight: style?.maxHeight || element?.style?.maxHeight || "",
          minHeight: style?.minHeight || element?.style?.minHeight || "",
          overscrollBehavior: style?.overscrollBehavior || element?.style?.overscrollBehavior || ""
        };
      }

      function isScrollableOverflow(value) {
        return ["auto", "scroll", "overlay"].includes(String(value || "").trim().toLowerCase());
      }

      function hasLiveGeometry(element) {
        return Boolean(
          safeNumber(element?.clientHeight) ||
          safeNumber(element?.clientWidth) ||
          safeNumber(element?.scrollHeight) ||
          safeNumber(element?.scrollWidth) ||
          rectFor(element).height ||
          rectFor(element).width
        );
      }

      function nearestScrollAncestor(element) {
        let parent = element?.parentElement || null;
        while (parent) {
          const style = computedStyleFor(parent);
          const vertical = isScrollableOverflow(style.overflowY || style.overflow);
          const horizontal = isScrollableOverflow(style.overflowX || style.overflow);
          if (vertical || horizontal || parent.getAttribute?.(attributes.scrollOwner) === "self") {
            return {
              tagName: parent.tagName || "",
              id: parent.id || "",
              mc: parent.getAttribute?.(attributes.type) || "",
              sourceIndex: parent.getAttribute?.(attributes.sourceIndex) || "",
              overflow: style.overflow,
              overflowX: style.overflowX,
              overflowY: style.overflowY
            };
          }
          parent = parent.parentElement;
        }
        return null;
      }

      function observeElement(element, options = {}) {
        const rect = rectFor(element);
        const computedStyle = computedStyleFor(element);
        const scrollHeight = safeNumber(element?.scrollHeight);
        const clientHeight = safeNumber(element?.clientHeight);
        const scrollWidth = safeNumber(element?.scrollWidth);
        const clientWidth = safeNumber(element?.clientWidth);
        const verticalOverflowPossible = scrollHeight > clientHeight + 1;
        const horizontalOverflowPossible = scrollWidth > clientWidth + 1;
        const verticalStyle = computedStyle.overflowY || computedStyle.overflow;
        const horizontalStyle = computedStyle.overflowX || computedStyle.overflow;
        const hasVerticalScrollbar = verticalOverflowPossible && isScrollableOverflow(verticalStyle);
        const hasHorizontalScrollbar = horizontalOverflowPossible && isScrollableOverflow(horizontalStyle);
        const clipped = (verticalOverflowPossible || horizontalOverflowPossible) && ["hidden", "clip"].includes(String(computedStyle.overflow || "").trim().toLowerCase());
        return {
          kind: "mcel-browser-observation",
          sourceIndex: element?.getAttribute?.(attributes.sourceIndex) || "",
          type: element?.getAttribute?.(attributes.type) || "",
          rect,
          scrollHeight,
          clientHeight,
          scrollWidth,
          clientWidth,
          computedStyle,
          hasLiveGeometry: hasLiveGeometry(element),
          verticalOverflowPossible,
          horizontalOverflowPossible,
          hasVerticalScrollbar,
          hasHorizontalScrollbar,
          hasInternalScrollbar: hasVerticalScrollbar || hasHorizontalScrollbar,
          clipped,
          viewportPressure: rect.bottom > (element?.ownerDocument?.defaultView?.innerHeight || Infinity) ? "outside-viewport" : "inside-viewport",
          nearestScrollAncestor: nearestScrollAncestor(element)
        };
      }

      function observeRoot(root, options = {}) {
        const elements = [...(root?.querySelectorAll?.(`[${attributes.type}]`) || [])];
        const observations = elements.map((element, index) => ({
          index,
          ...observeElement(element, options)
        }));
        return {
          kind: "mcel-browser-observer-report",
          elementCount: observations.length,
          observations,
          warnings: observations.length ? [] : ["No MCEL runtime elements were available for browser observation."]
        };
      }


      function ownerDocumentFor(root) {
        if (root?.nodeType === 9) return root;
        return root?.ownerDocument || (typeof document !== "undefined" ? document : null);
      }

      function sourceIndexFor(element) {
        return element?.getAttribute?.(attributes.sourceIndex) ||
          element?.closest?.(`[${attributes.sourceIndex}]`)?.getAttribute?.(attributes.sourceIndex) ||
          "";
      }

      function chromePartFor(element) {
        return element?.getAttribute?.("data-mcel-chrome-part") ||
          element?.closest?.("[data-mcel-chrome-part]")?.getAttribute?.("data-mcel-chrome-part") ||
          "";
      }

      function fitRegionFor(element) {
        return element?.getAttribute?.("data-mcel-fit-region") ||
          element?.closest?.("[data-mcel-fit-region]")?.getAttribute?.("data-mcel-fit-region") ||
          "";
      }

      function dataMcFor(element) {
        return element?.getAttribute?.(attributes.type) ||
          element?.closest?.(`[${attributes.type}]`)?.getAttribute?.(attributes.type) ||
          "";
      }

      function compositionWarningFor(element, problem, details = {}, tolerancePx = 2) {
        const rect = rectFor(element);
        return {
          problem,
          chromePart: chromePartFor(element),
          fitRegion: fitRegionFor(element),
          tagName: element?.tagName || "",
          className: classNameFor(element),
          sourceIndex: sourceIndexFor(element),
          dataMc: dataMcFor(element),
          elementWidth: Math.round(safeNumber(details.elementWidth ?? rect.width)),
          containerWidth: Math.round(safeNumber(details.containerWidth ?? rect.width)),
          inputWidth: Math.round(safeNumber(details.inputWidth ?? 0)),
          buttonWidth: Math.round(safeNumber(details.buttonWidth ?? 0)),
          delta: Math.round(safeNumber(details.delta ?? 0)),
          remedy: details.remedy || "",
          tolerancePx
        };
      }

      function classNameFor(element) {
        return typeof element?.className === "string" ? element.className : "";
      }

      function violationFor(element, problem, container, tolerancePx = 2, extra = {}) {
        const rect = rectFor(element);
        const containerRect = container ? rectFor(container) : {width: 0, left: 0, right: 0};
        return {
          problem,
          chromePart: chromePartFor(element) || chromePartFor(container),
          fitRegion: fitRegionFor(element) || fitRegionFor(container),
          tagName: element?.tagName || "",
          className: classNameFor(element),
          sourceIndex: sourceIndexFor(element),
          elementWidth: Math.round(safeNumber(extra.elementWidth ?? rect.width)),
          containerWidth: Math.round(safeNumber(extra.containerWidth ?? containerRect.width)),
          delta: Math.round(safeNumber(extra.delta ?? 0)),
          tolerancePx
        };
      }

      function uniqueElements(elements) {
        const seen = new Set();
        return elements.filter((element) => {
          if (!element || seen.has(element)) return false;
          seen.add(element);
          return true;
        });
      }

      function observableChromeElements(root, selectors = []) {
        const selectorText = selectors.length ? selectors.join(",") : [
          "[data-mcel-chrome-generated=\"true\"]",
          "[data-mcel-fit-region]",
          "[data-mcel-fit-policy]",
          `[${attributes.type}]`
        ].join(",");
        return uniqueElements([...(root?.querySelectorAll?.(selectorText) || [])]);
      }

      function nearestFitContainer(element) {
        return element?.closest?.("[data-mcel-fit-policy], [data-mcel-fit-region], [data-mcel-chrome-generated=\"true\"], [data-mc]") || element?.parentElement || null;
      }

      function isVisibleElement(element) {
        const rect = rectFor(element);
        const style = computedStyleFor(element);
        return rect.width > 0 && rect.height > 0 && style.display !== "none";
      }

      function allowedCompositionWarning(problem, compositionContract = {}) {
        const warnings = Array.isArray(compositionContract.warnings) ? compositionContract.warnings : [];
        return !warnings.length || warnings.includes(problem);
      }

      function remedyForCompositionWarning(problem, compositionContract = {}) {
        const remedies = compositionContract.remedies && typeof compositionContract.remedies === "object"
          ? compositionContract.remedies
          : {};
        return remedies[problem] || "";
      }

      function observableCompositionTargets(root, compositionContract = {}) {
        const fallbackSelectors = [
          ".mcel-chrome-editorial-rail > .mc",
          "[data-mcel-fit-region=\"narrow\"].mc",
          "[data-mcel-fit-region=\"narrow\"] > .mc"
        ];
        const selectors = Array.isArray(compositionContract.observeSelectors) && compositionContract.observeSelectors.length
          ? compositionContract.observeSelectors
          : fallbackSelectors;
        return uniqueElements(selectors.flatMap((selector) => {
          try {
            return [...(root?.querySelectorAll?.(selector) || [])];
          } catch (error) {
            return [];
          }
        })).filter(isVisibleElement);
      }

      function observeChromeComposition(root, options = {}) {
        const chrome = options.chrome || root?.getAttribute?.("data-mcel-chrome") || "";
        if (chrome !== "chrome-editorial-flow") return [];
        const tolerancePx = Number.isFinite(options.tolerancePx) ? options.tolerancePx : 2;
        const compositionContract = options.compositionContract || {};
        const warnings = [];
        const targets = observableCompositionTargets(root, compositionContract);

        targets.forEach((target) => {
          const controls = uniqueElements([...(target.querySelectorAll?.("input,textarea,select,button") || [])]).filter(isVisibleElement);
          const primaryInput = controls.find((control) => ["INPUT", "TEXTAREA", "SELECT"].includes(control.tagName || ""));
          const primaryButton = controls.find((control) => (control.tagName || "") === "BUTTON");
          if (!primaryInput || !primaryButton) return;

          const inputRect = rectFor(primaryInput);
          const buttonRect = rectFor(primaryButton);
          const problem = "primary-control-width-collapsed-relative-to-input";
          if (
            inputRect.width > tolerancePx &&
            buttonRect.width + tolerancePx < inputRect.width * 0.66 &&
            allowedCompositionWarning(problem, compositionContract)
          ) {
            warnings.push(compositionWarningFor(target, problem, {
              elementWidth: buttonRect.width,
              containerWidth: inputRect.width,
              inputWidth: inputRect.width,
              buttonWidth: buttonRect.width,
              delta: inputRect.width - buttonRect.width,
              remedy: remedyForCompositionWarning(problem, compositionContract)
            }, tolerancePx));
          }
        });

        return warnings;
      }

      function observeChromeFit(root, options = {}) {
        const doc = ownerDocumentFor(root);
        const scanRoot = root?.nodeType === 9 ? root.body : root;
        const chrome = options.chrome || scanRoot?.getAttribute?.("data-mcel-chrome") || doc?.body?.getAttribute?.("data-mcel-chrome") || "chrome-strict-hierarchy";
        const tolerancePx = Number.isFinite(options.tolerancePx) ? options.tolerancePx : 2;
        const selectors = Array.isArray(options.selectors) ? options.selectors : [];
        const hardObjectSelector = options.hardObjectSelector || "img,svg,canvas,video,iframe,table,pre,code,input,textarea,select,button";
        const compositionContract = options.compositionContract || {};
        const violations = [];

        if (!doc || !scanRoot) {
          return {
            kind: "mcel-chrome-fit-report",
            chrome,
            status: "unavailable",
            firstPassViolations: 0,
            finalViolations: 0,
            repaired: false,
            violationCount: 0,
            compositionWarningCount: 0,
            compositionWarnings: [],
            violations: [],
            warnings: ["Chrome fit observation could not access the rendered document."]
          };
        }

        const documentElement = doc.documentElement;
        const viewportWidth = safeNumber(documentElement?.clientWidth || doc.defaultView?.innerWidth);
        const pageScrollWidth = safeNumber(documentElement?.scrollWidth);
        if (viewportWidth && pageScrollWidth > viewportWidth + tolerancePx) {
          violations.push({
            problem: "page-overflow",
            chromePart: "document",
            fitRegion: "viewport",
            tagName: "HTML",
            className: "",
            sourceIndex: "",
            elementWidth: Math.round(pageScrollWidth),
            containerWidth: Math.round(viewportWidth),
            delta: Math.round(pageScrollWidth - viewportWidth),
            tolerancePx
          });
        }

        const observable = observableChromeElements(scanRoot, selectors).filter(isVisibleElement);
        observable.forEach((element) => {
          const clientWidth = safeNumber(element.clientWidth);
          const scrollWidth = safeNumber(element.scrollWidth);
          if (clientWidth && scrollWidth > clientWidth + tolerancePx) {
            violations.push(violationFor(element, "inline-overflow", element, tolerancePx, {
              elementWidth: scrollWidth,
              containerWidth: clientWidth,
              delta: scrollWidth - clientWidth
            }));
          }

          const parent = element.parentElement;
          if (parent && isVisibleElement(parent)) {
            const rect = rectFor(element);
            const parentRect = rectFor(parent);
            const escapes = rect.left < parentRect.left - tolerancePx || rect.right > parentRect.right + tolerancePx;
            if (escapes) {
              violations.push(violationFor(element, "child-escape", parent, tolerancePx, {
                elementWidth: rect.width,
                containerWidth: parentRect.width,
                delta: Math.max(0, rect.right - parentRect.right, parentRect.left - rect.left)
              }));
            }
          }
        });

        const hardObjects = uniqueElements([...(scanRoot.querySelectorAll?.(hardObjectSelector) || [])]).filter(isVisibleElement);
        hardObjects.forEach((element) => {
          const container = nearestFitContainer(element);
          if (!container || container === element || !isVisibleElement(container)) return;
          const rect = rectFor(element);
          const containerRect = rectFor(container);
          const isControl = ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(element.tagName || "");
          const internalInlineOverflow = !isControl && safeNumber(element.scrollWidth) > safeNumber(element.clientWidth) + tolerancePx;
          const overflow = rect.width > containerRect.width + tolerancePx ||
            rect.left < containerRect.left - tolerancePx ||
            rect.right > containerRect.right + tolerancePx ||
            internalInlineOverflow;
          if (overflow) {
            violations.push(violationFor(element, "hard-object-overflow", container, tolerancePx, {
              elementWidth: Math.max(rect.width, safeNumber(element.scrollWidth)),
              containerWidth: containerRect.width,
              delta: Math.max(0, rect.width - containerRect.width, rect.right - containerRect.right, containerRect.left - rect.left)
            }));
          }
        });

        const compositionWarnings = observeChromeComposition(scanRoot, {
          chrome,
          tolerancePx,
          compositionContract
        });
        const hasFitFailures = violations.length > 0 || compositionWarnings.length > 0;

        return {
          kind: "mcel-chrome-fit-report",
          chrome,
          status: hasFitFailures ? "failed" : "clean",
          firstPassViolations: violations.length,
          finalViolations: violations.length,
          repaired: false,
          violationCount: violations.length,
          compositionWarningCount: compositionWarnings.length,
          compositionWarnings: compositionWarnings.slice(0, 24),
          violations: violations.slice(0, 24),
          observedElementCount: observable.length,
          hardObjectCount: hardObjects.length,
          tolerancePx,
          warnings: []
        };
      }

      function compactReport(root, options = {}) {
        const report = observeRoot(root, options);
        return {
          kind: report.kind,
          elementCount: report.elementCount,
          scrollbars: report.observations.filter((item) => item.hasInternalScrollbar).length,
          clipped: report.observations.filter((item) => item.clipped).length,
          liveGeometry: report.observations.filter((item) => item.hasLiveGeometry).length
        };
      }

      return Object.freeze({
        observeElement,
        observeRoot,
        observeChromeFit,
        observeChromeComposition,
        compactReport,
        nearestScrollAncestor
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabBrowserObserver = McelLabBrowserObserver;
    }
