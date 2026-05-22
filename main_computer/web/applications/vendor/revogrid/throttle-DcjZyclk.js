/*!
 * Built by Revolist OU ❤️
 */
import { q as scaleValue } from './dimension.helpers-D5lwLPzd.js';
import { d as debounce, a as isObject } from './debounce-BfO9dz9v.js';

const initialParams = {
    contentSize: 0,
    clientSize: 0,
    virtualSize: 0,
    maxSize: 0,
};
const NO_COORDINATE = -1;
/**
 * Based on content size, client size and virtual size
 * return full size
 */
function getContentSize(contentSize, clientSize, virtualSize = 0) {
    if (virtualSize > contentSize) {
        return 0;
    }
    return contentSize + (virtualSize ? clientSize - virtualSize : 0);
}
class LocalScrollService {
    constructor(cfg) {
        this.cfg = cfg;
        this.preventArtificialScroll = {
            rgRow: null,
            rgCol: null,
        };
        // to check if scroll changed
        this.previousScroll = {
            rgRow: NO_COORDINATE,
            rgCol: NO_COORDINATE,
        };
        this.params = {
            rgRow: Object.assign({}, initialParams),
            rgCol: Object.assign({}, initialParams),
        };
    }
    setParams(params, dimension) {
        const virtualContentSize = getContentSize(params.contentSize, params.clientSize, params.virtualSize);
        this.params[dimension] = Object.assign(Object.assign({}, params), { maxSize: virtualContentSize - params.clientSize, virtualContentSize });
    }
    // apply scroll values after scroll done
    async setScroll(e) {
        this.cancelScroll(e.dimension);
        // start frame animation
        const frameAnimation = new Promise((resolve, reject) => {
            // for example safari desktop has issues with animation frame
            if (this.cfg.skipAnimationFrame) {
                return resolve();
            }
            const animationId = window.requestAnimationFrame(() => {
                resolve();
            });
            this.preventArtificialScroll[e.dimension] = reject.bind(null, animationId);
        });
        try {
            await frameAnimation;
            const params = this.getParams(e.dimension);
            e.coordinate = Math.ceil(e.coordinate);
            this.previousScroll[e.dimension] = this.wrapCoordinate(e.coordinate, params);
            this.preventArtificialScroll[e.dimension] = null;
            this.cfg.applyScroll(Object.assign(Object.assign({}, e), { coordinate: params.virtualSize
                    ? this.convert(e.coordinate, params, false)
                    : e.coordinate }));
        }
        catch (id) {
            window.cancelAnimationFrame(id);
        }
    }
    /**
     * On scroll event started
     */
    scroll(coordinate, dimension, force = false, delta, outside = false) {
        // cancel all previous scrolls for same dimension
        this.cancelScroll(dimension);
        // drop if no change
        if (!force && this.previousScroll[dimension] === coordinate) {
            this.previousScroll[dimension] = NO_COORDINATE;
            return;
        }
        const param = this.getParams(dimension);
        // let component know about scroll event started
        this.cfg.runScroll({
            dimension: dimension,
            coordinate: param.virtualSize
                ? this.convert(coordinate, param)
                : coordinate,
            delta,
            outside,
        });
    }
    getParams(dimension) {
        return this.params[dimension];
    }
    // check if scroll outside of region to avoid looping
    wrapCoordinate(c, param) {
        if (c < 0) {
            return NO_COORDINATE;
        }
        if (typeof param.maxSize === 'number' && c > param.maxSize) {
            return param.maxSize;
        }
        return c;
    }
    // prevent already started scroll, performance optimization
    cancelScroll(dimension) {
        var _a, _b;
        (_b = (_a = this.preventArtificialScroll)[dimension]) === null || _b === void 0 ? void 0 : _b.call(_a);
        this.preventArtificialScroll[dimension] = null;
    }
    /* convert virtual to real and back, scale range */
    convert(pos, param, toReal = true) {
        var _a;
        const minRange = param.clientSize;
        const from = [0, ((_a = param.virtualContentSize) !== null && _a !== void 0 ? _a : minRange) - minRange];
        const to = [0, param.contentSize - param.virtualSize];
        if (toReal) {
            return scaleValue(pos, from, to);
        }
        return scaleValue(pos, to, from);
    }
}

/**
 * Apply changes only if mousewheel event happened some time ago (scrollThrottling)
 */
class LocalScrollTimer {
    constructor(scrollThrottling = 10) {
        this.scrollThrottling = scrollThrottling;
        /**
         * Last mw event time for trigger scroll function below
         * If mousewheel function was ignored we still need to trigger render
         */
        this.mouseWheelScrollTimestamp = {
            rgCol: 0,
            rgRow: 0,
        };
        this.lastKnownScrollCoordinate = {
            rgCol: 0,
            rgRow: 0,
        };
        /**
         * Check if scroll is ready to accept new value
         * this is an edge case for scroll events
         * when we need to apply scroll after throttling
         */
        this.lastScrollUpdateCallbacks = {};
    }
    setCoordinate(e) {
        this.lastKnownScrollCoordinate[e.dimension] = e.coordinate;
    }
    /**
     * Remember last mw event time
     */
    latestScrollUpdate(dimension) {
        this.mouseWheelScrollTimestamp[dimension] = new Date().getTime();
    }
    isReady(type, coordinate) {
        // if there is a callback, clear it
        if (this.lastScrollUpdateCallbacks[type]) {
            this.clearLastScrollUpdate(type);
        }
        // apply after throttling
        return this.verifyChange(type, coordinate);
    }
    verifyChange(type, coordinate) {
        const now = new Date().getTime();
        const change = now - this.mouseWheelScrollTimestamp[type];
        return change > this.scrollThrottling &&
            coordinate !== this.lastKnownScrollCoordinate[type];
    }
    clearLastScrollUpdate(type) {
        var _a, _b;
        clearTimeout((_b = (_a = this.lastScrollUpdateCallbacks[type]) === null || _a === void 0 ? void 0 : _a.timeout) !== null && _b !== void 0 ? _b : 0);
        delete this.lastScrollUpdateCallbacks[type];
    }
    throttleLastScrollUpdate(type, coordinate, lastScrollUpdate) {
        // if scrollThrottling is set
        // we need to throttle the last scroll event
        if (this.scrollThrottling) {
            this.clearLastScrollUpdate(type);
            // save lastScrollUpdate callback
            const callback = this.lastScrollUpdateCallbacks[type] = {
                callback: lastScrollUpdate,
                timestamp: new Date().getTime(),
                coordinate,
                timeout: 0,
            };
            callback.timeout = setTimeout(() => {
                // clear timeout
                this.clearLastScrollUpdate(type);
                // if scrollThrottling is set, and the last scroll event happened before the timeout started
                // we need to throttle the last scroll event
                if (this.mouseWheelScrollTimestamp[type] < callback.timestamp && this.verifyChange(type, callback.coordinate)) {
                    callback.callback();
                }
            }, this.scrollThrottling + 50);
        }
    }
}

/** Error message constants. */
var FUNC_ERROR_TEXT = 'Expected a function';

/**
 * Creates a throttled function that only invokes `func` at most once per
 * every `wait` milliseconds. The throttled function comes with a `cancel`
 * method to cancel delayed `func` invocations and a `flush` method to
 * immediately invoke them. Provide `options` to indicate whether `func`
 * should be invoked on the leading and/or trailing edge of the `wait`
 * timeout. The `func` is invoked with the last arguments provided to the
 * throttled function. Subsequent calls to the throttled function return the
 * result of the last `func` invocation.
 *
 * **Note:** If `leading` and `trailing` options are `true`, `func` is
 * invoked on the trailing edge of the timeout only if the throttled function
 * is invoked more than once during the `wait` timeout.
 *
 * If `wait` is `0` and `leading` is `false`, `func` invocation is deferred
 * until to the next tick, similar to `setTimeout` with a timeout of `0`.
 *
 * See [David Corbacho's article](https://css-tricks.com/debouncing-throttling-explained-examples/)
 * for details over the differences between `_.throttle` and `_.debounce`.
 *
 * @static
 * @memberOf _
 * @since 0.1.0
 * @category Function
 * @param {Function} func The function to throttle.
 * @param {number} [wait=0] The number of milliseconds to throttle invocations to.
 * @param {Object} [options={}] The options object.
 * @param {boolean} [options.leading=true]
 *  Specify invoking on the leading edge of the timeout.
 * @param {boolean} [options.trailing=true]
 *  Specify invoking on the trailing edge of the timeout.
 * @returns {Function} Returns the new throttled function.
 * @example
 *
 * // Avoid excessively updating the position while scrolling.
 * jQuery(window).on('scroll', _.throttle(updatePosition, 100));
 *
 * // Invoke `renewToken` when the click event is fired, but not more than once every 5 minutes.
 * var throttled = _.throttle(renewToken, 300000, { 'trailing': false });
 * jQuery(element).on('click', throttled);
 *
 * // Cancel the trailing throttled invocation.
 * jQuery(window).on('popstate', throttled.cancel);
 */
function throttle(func, wait, options) {
  var leading = true,
      trailing = true;

  if (typeof func != 'function') {
    throw new TypeError(FUNC_ERROR_TEXT);
  }
  if (isObject(options)) {
    leading = 'leading' in options ? !!options.leading : leading;
    trailing = 'trailing' in options ? !!options.trailing : trailing;
  }
  return debounce(func, wait, {
    'leading': leading,
    'maxWait': wait,
    'trailing': trailing
  });
}

export { LocalScrollTimer as L, LocalScrollService as a, getContentSize as g, throttle as t };
//# sourceMappingURL=throttle-DcjZyclk.js.map

//# sourceMappingURL=throttle-DcjZyclk.js.map