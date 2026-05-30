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
        compactReport,
        nearestScrollAncestor
      });
    })();

    if (typeof window !== "undefined") {
      window.McelLabBrowserObserver = McelLabBrowserObserver;
    }
