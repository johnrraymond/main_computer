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
          overscrollBehavior: style?.overscrollBehavior || element?.style?.overscrollBehavior || "",
          fontSize: style?.fontSize || element?.style?.fontSize || "",
          lineHeight: style?.lineHeight || element?.style?.lineHeight || "",
          whiteSpace: style?.whiteSpace || element?.style?.whiteSpace || "",
          wordBreak: style?.wordBreak || element?.style?.wordBreak || "",
          overflowWrap: style?.overflowWrap || element?.style?.overflowWrap || "",
          writingMode: style?.writingMode || element?.style?.writingMode || "",
          textOrientation: style?.textOrientation || element?.style?.textOrientation || ""
        };
      }

      function isScrollableOverflow(value) {
        return ["auto", "scroll", "overlay"].includes(String(value || "").trim().toLowerCase());
      }

      function parseCssPixelValue(value, fallback = 0) {
        const parsed = parseFloat(String(value || "").replace("px", ""));
        return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
      }

      function lineHeightFor(style) {
        const fontSize = parseCssPixelValue(style?.fontSize, 16);
        const raw = String(style?.lineHeight || "").trim().toLowerCase();
        if (!raw || raw === "normal") return fontSize * 1.2;
        if (raw.endsWith("px")) return parseCssPixelValue(raw, fontSize * 1.2);
        const numeric = parseFloat(raw);
        if (Number.isFinite(numeric) && numeric > 0) {
          return numeric < 4 ? numeric * fontSize : numeric;
        }
        return fontSize * 1.2;
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

      function parseRadiusComponent(value, axisSize) {
        const raw = String(value || "0").trim();
        if (!raw) return 0;
        if (raw.endsWith("%")) return safeNumber(parseFloat(raw)) * axisSize / 100;
        return safeNumber(parseFloat(raw));
      }

      function parseRadiusPair(value, width, height) {
        const parts = String(value || "0").trim().split(/\s+/).filter(Boolean);
        const rx = parseRadiusComponent(parts[0] || "0", width);
        const ry = parseRadiusComponent(parts[1] || parts[0] || "0", height);
        return {rx, ry};
      }

      function normalizedCornerRadii(element) {
        const rect = rectFor(element);
        const style = element?.ownerDocument?.defaultView?.getComputedStyle
          ? element.ownerDocument.defaultView.getComputedStyle(element)
          : null;
        const radii = {
          tl: parseRadiusPair(style?.borderTopLeftRadius, rect.width, rect.height),
          tr: parseRadiusPair(style?.borderTopRightRadius, rect.width, rect.height),
          br: parseRadiusPair(style?.borderBottomRightRadius, rect.width, rect.height),
          bl: parseRadiusPair(style?.borderBottomLeftRadius, rect.width, rect.height)
        };
        const scale = Math.min(
          1,
          rect.width / Math.max(1, radii.tl.rx + radii.tr.rx),
          rect.width / Math.max(1, radii.bl.rx + radii.br.rx),
          rect.height / Math.max(1, radii.tl.ry + radii.bl.ry),
          rect.height / Math.max(1, radii.tr.ry + radii.br.ry)
        );
        Object.keys(radii).forEach((key) => {
          radii[key].rx *= scale;
          radii[key].ry *= scale;
        });
        return radii;
      }

      function shapeSummaryFor(element) {
        const rect = rectFor(element);
        const radii = normalizedCornerRadii(element);
        const minRadius = Math.min(
          radii.tl.rx, radii.tr.rx, radii.br.rx, radii.bl.rx,
          radii.tl.ry, radii.tr.ry, radii.br.ry, radii.bl.ry
        );
        const minSide = Math.min(rect.width, rect.height);
        const aspect = rect.height ? rect.width / rect.height : 0;
        let shape = "flat-or-unknown";
        if (minSide > 0 && minRadius >= minSide * 0.43 && aspect > 1.25) {
          shape = "pill-oval";
        } else if (minSide > 0 && minRadius >= minSide * 0.43) {
          shape = "circle-ish";
        } else if (minRadius >= 24) {
          shape = "large-rounded-card";
        } else if (minRadius > 0) {
          shape = "rounded-card";
        }
        return {
          shape,
          aspect,
          minRadius,
          minRadiusToMinSide: minSide ? minRadius / minSide : 0
        };
      }

      function isShapeInteriorSensitive(element) {
        const summary = shapeSummaryFor(element);
        return summary.shape === "pill-oval" || summary.shape === "circle-ish";
      }

      function safeShapeIntervalAtY(element, y, insetPx = 6) {
        const rect = rectFor(element);
        const radii = normalizedCornerRadii(element);
        const left = rect.left + insetPx;
        const right = rect.right - insetPx;
        const top = rect.top + insetPx;
        const bottom = rect.bottom - insetPx;
        if (y < top || y > bottom) {
          return {left, right, width: Math.max(0, right - left), outsideY: true};
        }

        let safeLeft = left;
        let safeRight = right;

        const leftCornerBound = (corner, cx, cy) => {
          const rx = Math.max(0, corner.rx - insetPx);
          const ry = Math.max(0, corner.ry - insetPx);
          if (rx <= 0 || ry <= 0) return left;
          const dy = Math.abs(y - cy);
          if (dy >= ry) return left;
          return cx - rx * Math.sqrt(Math.max(0, 1 - (dy * dy) / (ry * ry)));
        };

        const rightCornerBound = (corner, cx, cy) => {
          const rx = Math.max(0, corner.rx - insetPx);
          const ry = Math.max(0, corner.ry - insetPx);
          if (rx <= 0 || ry <= 0) return right;
          const dy = Math.abs(y - cy);
          if (dy >= ry) return right;
          return cx + rx * Math.sqrt(Math.max(0, 1 - (dy * dy) / (ry * ry)));
        };

        if (y < rect.top + radii.tl.ry) {
          safeLeft = Math.max(safeLeft, leftCornerBound(radii.tl, rect.left + radii.tl.rx, rect.top + radii.tl.ry));
        }
        if (y < rect.top + radii.tr.ry) {
          safeRight = Math.min(safeRight, rightCornerBound(radii.tr, rect.right - radii.tr.rx, rect.top + radii.tr.ry));
        }
        if (y > rect.bottom - radii.bl.ry) {
          safeLeft = Math.max(safeLeft, leftCornerBound(radii.bl, rect.left + radii.bl.rx, rect.bottom - radii.bl.ry));
        }
        if (y > rect.bottom - radii.br.ry) {
          safeRight = Math.min(safeRight, rightCornerBound(radii.br, rect.right - radii.br.rx, rect.bottom - radii.br.ry));
        }

        return {
          left: safeLeft,
          right: safeRight,
          width: Math.max(0, safeRight - safeLeft),
          outsideY: false
        };
      }

      function shapeInteriorEscapeFor(target, child, tolerancePx = 2) {
        const rect = rectFor(child);
        const sampleYs = [
          rect.top + 1,
          rect.top + rect.height / 2,
          rect.bottom - 1
        ].filter((value) => Number.isFinite(value));
        const failures = sampleYs.map((y) => {
          const interval = safeShapeIntervalAtY(target, y);
          const leftDelta = Math.max(0, interval.left - rect.left);
          const rightDelta = Math.max(0, rect.right - interval.right);
          const delta = Math.max(leftDelta, rightDelta);
          return {
            y,
            safeLeft: interval.left,
            safeRight: interval.right,
            safeWidth: interval.width,
            childLeft: rect.left,
            childRight: rect.right,
            childWidth: rect.width,
            leftDelta,
            rightDelta,
            delta,
            escaped: interval.outsideY || delta > tolerancePx
          };
        }).filter((failure) => failure.escaped);
        if (!failures.length) return null;
        return failures.reduce((worst, failure) => failure.delta > worst.delta ? failure : worst, failures[0]);
      }

      function shapeContainmentChildrenFor(target) {
        const structuralSelectors = [
          "[data-mc=\"panel\"]",
          ".mc-panel",
          "article[data-mc]",
          "[data-mc-component-kind=\"component\"]"
        ].join(",");
        const structural = uniqueElements([...(target?.querySelectorAll?.(structuralSelectors) || [])])
          .filter((child) => child !== target && isVisibleElement(child));
        if (structural.length) return structural;

        return uniqueElements([...(target?.querySelectorAll?.([
          "h1", "h2", "h3", "h4", "h5", "h6",
          "p", "label", "input", "textarea", "select", "button", "a",
          "img", "svg", "canvas", "video", "iframe", "table", "pre", "code"
        ].join(",")) || [])]).filter(isVisibleElement);
      }

      function shortTextFor(element) {
        return String(element?.value || element?.textContent || element?.getAttribute?.("placeholder") || "")
          .trim()
          .replace(/\s+/g, " ")
          .slice(0, 120);
      }

      function isActionTextElement(element) {
        const tagName = element?.tagName || "";
        return tagName === "BUTTON" ||
          (tagName === "A" && element.getAttribute?.("data-mc-action")) ||
          element?.getAttribute?.("role") === "button";
      }

      function textDistortionFor(element, tolerancePx = 2) {
        if (!isActionTextElement(element)) return null;
        const text = shortTextFor(element);
        const glyphCount = text.replace(/\s+/g, "").length;
        if (glyphCount < 6) return null;

        const rect = rectFor(element);
        if (rect.width <= tolerancePx || rect.height <= tolerancePx) return null;

        const style = computedStyleFor(element);
        const fontSize = parseCssPixelValue(style.fontSize, 16);
        const lineHeight = lineHeightFor(style);
        const lineCount = rect.height / Math.max(1, lineHeight);
        const longestWord = Math.max(0, ...text.split(/\s+/).map((word) => word.length));
        const characterCapacity = rect.width / Math.max(1, fontSize * 0.56);
        const inlineEstimate = glyphCount * fontSize * 0.56 + Math.max(24, text.split(/\s+/).length * fontSize * 0.35);
        const narrowByEstimate = rect.width + tolerancePx < Math.min(inlineEstimate * 0.58, 120);
        const stackedWord = longestWord >= 4 && characterCapacity + 0.75 < longestWord && lineCount >= 2.6;
        const hyperTall = lineCount >= 3.5 && rect.width < Math.min(96, inlineEstimate * 0.65);
        const verticalWriting = String(style.writingMode || "").trim().toLowerCase().startsWith("vertical");

        if (!(verticalWriting || stackedWord || hyperTall || (narrowByEstimate && lineCount >= 2.6))) {
          return null;
        }

        return {
          text,
          glyphCount,
          lineCount,
          characterCapacity,
          inlineEstimate,
          fontSize,
          lineHeight,
          verticalWriting,
          distortionRatio: lineCount / Math.max(1, Math.ceil(glyphCount / Math.max(1, characterCapacity)))
        };
      }

      function isPlainActionOrFieldContainer(element) {
        const tagName = element?.tagName || "";
        return tagName === "BUTTON" ||
          tagName === "A" ||
          tagName === "INPUT" ||
          tagName === "TEXTAREA" ||
          tagName === "SELECT" ||
          element?.getAttribute?.("role") === "button";
      }

      function containerDistortionFor(element, tolerancePx = 2) {
        if (isPlainActionOrFieldContainer(element)) return null;
        const rect = rectFor(element);
        if (rect.width <= tolerancePx || rect.height <= tolerancePx) return null;

        const summary = shapeSummaryFor(element);
        if (summary.shape !== "pill-oval" && summary.shape !== "circle-ish") return null;

        const directVisibleChildren = [...(element?.children || [])].filter(isVisibleElement);
        const structuralChildren = uniqueElements([...(element?.querySelectorAll?.([
          "[data-mcel-chrome-region-role]",
          "[data-mcel-chrome-frame]",
          "[data-mc]",
          ".mc",
          "section",
          "article",
          "form",
          "main"
        ].join(",")) || [])]).filter(isVisibleElement);
        const visibleChildCount = Math.max(directVisibleChildren.length, structuralChildren.length);
        const text = shortTextFor(element);
        const generatedChromeContainer = element?.getAttribute?.("data-mcel-chrome-generated") === "true" || Boolean(chromePartFor(element));
        const complexContent = visibleChildCount >= 2 ||
          text.length >= 64 ||
          Boolean(element?.querySelector?.("section,article,form,main,[data-mc-component-kind=\"layout\"],[data-mc-component-kind=\"island\"]"));
        const aspect = rect.height ? rect.width / rect.height : 0;
        const widePill = summary.shape === "pill-oval" && aspect >= 1.55 && rect.height >= 96;
        const largeCircle = summary.shape === "circle-ish" && Math.min(rect.width, rect.height) >= 180;

        if (!generatedChromeContainer && !complexContent) return null;
        if (!complexContent || !(widePill || largeCircle)) return null;

        return {
          text,
          shape: summary.shape,
          aspectRatio: aspect,
          minRadius: summary.minRadius,
          minRadiusToMinSide: summary.minRadiusToMinSide,
          visibleChildCount,
          elementWidth: rect.width,
          elementHeight: rect.height,
          distortionRatio: Math.max(aspect, aspect ? 1 / aspect : 0)
        };
      }

      function compositionWarningFor(element, problem, details = {}, tolerancePx = 2) {
        const rect = rectFor(element);
        return {
          problem,
          chromePart: chromePartFor(element),
          fitRegion: fitRegionFor(element),
          tagName: element?.tagName || "",
          className: classNameFor(element),
          sourceIndex: sourceIndexFor(element) || sourceIndexFor(element?.querySelector?.(`[${attributes.sourceIndex}]`)),
          dataMc: dataMcFor(element),
          elementWidth: Math.round(safeNumber(details.elementWidth ?? rect.width)),
          containerWidth: Math.round(safeNumber(details.containerWidth ?? rect.width)),
          inputWidth: Math.round(safeNumber(details.inputWidth ?? 0)),
          buttonWidth: Math.round(safeNumber(details.buttonWidth ?? 0)),
          childTagName: details.childTagName || "",
          childText: details.childText || "",
          shape: details.shape || "",
          aspectRatio: Math.round(safeNumber(details.aspectRatio ?? 0) * 10) / 10,
          minRadius: Math.round(safeNumber(details.minRadius ?? 0)),
          visibleChildCount: Math.round(safeNumber(details.visibleChildCount ?? 0)),
          safeWidth: Math.round(safeNumber(details.safeWidth ?? 0)),
          leftDelta: Math.round(safeNumber(details.leftDelta ?? 0)),
          rightDelta: Math.round(safeNumber(details.rightDelta ?? 0)),
          delta: Math.round(safeNumber(details.delta ?? 0)),
          lineCount: Math.round(safeNumber(details.lineCount ?? 0) * 10) / 10,
          characterCapacity: Math.round(safeNumber(details.characterCapacity ?? 0) * 10) / 10,
          inlineEstimate: Math.round(safeNumber(details.inlineEstimate ?? 0)),
          distortionRatio: Math.round(safeNumber(details.distortionRatio ?? 0) * 10) / 10,
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

      function chromeSupportsCompositionObservation(compositionContract = {}) {
        const selectors = Array.isArray(compositionContract.observeSelectors) ? compositionContract.observeSelectors : [];
        const warnings = Array.isArray(compositionContract.warnings) ? compositionContract.warnings : [];
        return selectors.length > 0 && warnings.length > 0;
      }

      function remedyForCompositionWarning(problem, compositionContract = {}) {
        const remedies = compositionContract.remedies && typeof compositionContract.remedies === "object"
          ? compositionContract.remedies
          : {};
        return remedies[problem] || "";
      }

      function observableCompositionTargets(root, compositionContract = {}) {
        const selectors = Array.isArray(compositionContract.observeSelectors) && compositionContract.observeSelectors.length
          ? compositionContract.observeSelectors
          : [];
        return uniqueElements(selectors.flatMap((selector) => {
          try {
            return [...(root?.querySelectorAll?.(selector) || [])];
          } catch (error) {
            return [];
          }
        })).filter(isVisibleElement);
      }

      function observeChromeComposition(root, options = {}) {
        const tolerancePx = Number.isFinite(options.tolerancePx) ? options.tolerancePx : 2;
        const compositionContract = options.compositionContract || {};
        if (!chromeSupportsCompositionObservation(compositionContract)) return [];
        const warnings = [];
        const targets = observableCompositionTargets(root, compositionContract);

        targets.forEach((target) => {
          const controls = uniqueElements([...(target.querySelectorAll?.("input,textarea,select,button") || [])]).filter(isVisibleElement);
          const primaryInput = controls.find((control) => ["INPUT", "TEXTAREA", "SELECT"].includes(control.tagName || ""));
          const primaryButton = controls.find((control) => (control.tagName || "") === "BUTTON");
          if (primaryInput && primaryButton) {
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
          }

          const distortionProblem = "text-distorted-by-narrow-inline-size";
          if (allowedCompositionWarning(distortionProblem, compositionContract)) {
            const actionTextElements = uniqueElements([...(target.querySelectorAll?.("button,a[data-mc-action],[role=\"button\"]") || [])]).filter(isVisibleElement);
            let worstDistortion = null;
            actionTextElements.forEach((child) => {
              const distortion = textDistortionFor(child, tolerancePx);
              if (!distortion) return;
              if (!worstDistortion || distortion.lineCount > worstDistortion.distortion.lineCount) {
                worstDistortion = {child, distortion};
              }
            });
            if (worstDistortion) {
              const childRect = rectFor(worstDistortion.child);
              warnings.push(compositionWarningFor(worstDistortion.child, distortionProblem, {
                elementWidth: childRect.width,
                containerWidth: Math.max(childRect.width, worstDistortion.distortion.inlineEstimate),
                childTagName: worstDistortion.child?.tagName || "",
                childText: worstDistortion.distortion.text,
                lineCount: worstDistortion.distortion.lineCount,
                characterCapacity: worstDistortion.distortion.characterCapacity,
                inlineEstimate: worstDistortion.distortion.inlineEstimate,
                distortionRatio: worstDistortion.distortion.distortionRatio,
                delta: Math.max(0, worstDistortion.distortion.inlineEstimate - childRect.width),
                remedy: remedyForCompositionWarning(distortionProblem, compositionContract)
              }, tolerancePx));
            }
          }

          const containerProblem = "container-distorted-by-extreme-aspect-ratio";
          if (allowedCompositionWarning(containerProblem, compositionContract)) {
            const containerDistortion = containerDistortionFor(target, tolerancePx);
            if (containerDistortion) {
              warnings.push(compositionWarningFor(target, containerProblem, {
                elementWidth: containerDistortion.elementWidth,
                containerWidth: containerDistortion.elementHeight,
                childTagName: target?.tagName || "",
                childText: containerDistortion.text,
                shape: containerDistortion.shape,
                aspectRatio: containerDistortion.aspectRatio,
                minRadius: containerDistortion.minRadius,
                visibleChildCount: containerDistortion.visibleChildCount,
                distortionRatio: containerDistortion.distortionRatio,
                delta: Math.max(0, containerDistortion.minRadius - Math.min(28, containerDistortion.elementHeight / 4)),
                remedy: remedyForCompositionWarning(containerProblem, compositionContract)
              }, tolerancePx));
            }
          }

          const shapeContainmentProblem = "shape-containment-failed";
          const shapeInteriorProblem = "shape-interior-escape";
          const shapeProblem = allowedCompositionWarning(shapeContainmentProblem, compositionContract)
            ? shapeContainmentProblem
            : shapeInteriorProblem;
          if (!allowedCompositionWarning(shapeProblem, compositionContract) || !isShapeInteriorSensitive(target)) {
            return;
          }

          const content = shapeContainmentChildrenFor(target);
          const shape = shapeSummaryFor(target);
          let worst = null;
          content.forEach((child) => {
            const escape = shapeInteriorEscapeFor(target, child, tolerancePx);
            if (!escape) return;
            if (!worst || escape.delta > worst.escape.delta) {
              worst = {child, escape};
            }
          });
          if (worst) {
            const childRect = rectFor(worst.child);
            warnings.push(compositionWarningFor(target, shapeProblem, {
              elementWidth: childRect.width,
              containerWidth: worst.escape.safeWidth,
              safeWidth: worst.escape.safeWidth,
              leftDelta: worst.escape.leftDelta,
              rightDelta: worst.escape.rightDelta,
              delta: worst.escape.delta,
              childTagName: worst.child?.tagName || "",
              childText: shortTextFor(worst.child),
              shape: shape.shape,
              visibleChildCount: content.length,
              remedy: remedyForCompositionWarning(shapeProblem, compositionContract)
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
