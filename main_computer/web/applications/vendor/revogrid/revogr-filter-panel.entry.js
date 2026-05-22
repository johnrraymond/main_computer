/*!
 * Built by Revolist OU ❤️
 */
import { r as registerInstance, c as createEvent, h, H as Host, g as getElement } from './index-Dyptvvxf.js';
import { d as debounce } from './debounce-BfO9dz9v.js';
import { i as isFilterBtn, e as AndOrButton, d as TrashButton } from './filter.button-DmOE7VCJ.js';

(function closest() {
    if (!Element.prototype.matches) {
        Element.prototype.matches =
            Element.prototype.msMatchesSelector || Element.prototype.webkitMatchesSelector;
    }
    if (!Element.prototype.closest) {
        Element.prototype.closest = function (s) {
            let el = this;
            do {
                if (Element.prototype.matches.call(el, s)) {
                    return el;
                }
                el = el.parentElement || el.parentNode;
            } while (el !== null && el.nodeType === 1);
            return null;
        };
    }
})();

const filterStyleCss = "revogr-filter-panel{position:absolute;display:block;top:0;left:0;z-index:100;max-height:calc(100% - 80px);min-width:250px;overflow:auto;opacity:1;transform:none;background-color:var(--revo-grid-filter-panel-bg, #fff);border:1px solid var(--revo-grid-filter-panel-border, #cecece);transform-origin:62px 0px;box-shadow:0 5px 18px -2px var(--revo-grid-filter-panel-shadow, rgba(0, 0, 0, 0.15));padding:10px;border-radius:8px;min-width:220px;text-align:left}revogr-filter-panel .filter-holder>div{display:flex;flex-direction:column}revogr-filter-panel label{font-size:13px;display:block;padding:8px 0}revogr-filter-panel select{width:100%}revogr-filter-panel input[type=text]{border:0;min-height:34px;margin:5px 0;background:var(--revo-grid-filter-panel-input-bg, #f3f3f3);border-radius:5px;padding:0 10px;box-sizing:border-box;width:100%}revogr-filter-panel button{margin-top:10px;margin-right:5px}revogr-filter-panel .filter-actions{text-align:right;margin-right:-5px}.rgHeaderCell:hover .rv-filter{transition:opacity 267ms cubic-bezier(0.4, 0, 0.2, 1) 0ms, transform 178ms cubic-bezier(0.4, 0, 0.2, 1) 0ms}.rgHeaderCell:hover .rv-filter,.rgHeaderCell .rv-filter.active{opacity:1}.rgHeaderCell .rv-filter{height:24px;width:24px;background:none;border:0;opacity:0;visibility:visible;cursor:pointer;border-radius:4px}.rgHeaderCell .rv-filter.active{color:#10224a}.rgHeaderCell .rv-filter .filter-img{color:gray;width:11px}.select-css{display:block;font-family:sans-serif;line-height:1.3;padding:0.6em 1.4em 0.5em 0.8em;width:100%;max-width:100%;box-sizing:border-box;margin:0;border:1px solid var(--revo-grid-filter-panel-select-border, #d9d9d9);box-shadow:transparent;border-radius:0.5em;appearance:none;background-color:var(--revo-grid-filter-panel-input-bg, #f3f3f3);background-image:url(\"data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%22292.4%22%20height%3D%22292.4%22%3E%3Cpath%20fill%3D%22%23007CB2%22%20d%3D%22M287%2069.4a17.6%2017.6%200%200%200-13-5.4H18.4c-5%200-9.3%201.8-12.9%205.4A17.6%2017.6%200%200%200%200%2082.2c0%205%201.8%209.3%205.4%2012.9l128%20127.9c3.6%203.6%207.8%205.4%2012.8%205.4s9.2-1.8%2012.8-5.4L287%2095c3.5-3.5%205.4-7.8%205.4-12.8%200-5-1.9-9.2-5.5-12.8z%22%2F%3E%3C%2Fsvg%3E\");background-repeat:no-repeat, repeat;background-position:right 0.7em top 50%, 0 0;background-size:0.65em auto, 100%;}.select-css::-ms-expand{display:none}.select-css:hover{border-color:var(--revo-grid-filter-panel-select-border, #d9d9d9)}.select-css:focus{border-color:var(--revo-grid-filter-panel-select-border-hover, #d9d9d9);box-shadow:0 0 1px 3px rgba(59, 153, 252, 0.7);box-shadow:0 0 0 3px -moz-mac-focusring;outline:none}.select-css option{font-weight:normal}.select-css:disabled,.select-css[aria-disabled=true]{color:gray;background-image:url(\"data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%22292.4%22%20height%3D%22292.4%22%3E%3Cpath%20fill%3D%22graytext%22%20d%3D%22M287%2069.4a17.6%2017.6%200%200%200-13-5.4H18.4c-5%200-9.3%201.8-12.9%205.4A17.6%2017.6%200%200%200%200%2082.2c0%205%201.8%209.3%205.4%2012.9l128%20127.9c3.6%203.6%207.8%205.4%2012.8%205.4s9.2-1.8%2012.8-5.4L287%2095c3.5-3.5%205.4-7.8%205.4-12.8%200-5-1.9-9.2-5.5-12.8z%22%2F%3E%3C%2Fsvg%3E\"), linear-gradient(to bottom, #ffffff 0%, #ffffff 100%)}.select-css:disabled:hover,.select-css[aria-disabled=true]{border-color:var(--revo-grid-filter-panel-select-border, #d9d9d9)}.multi-filter-list{margin-top:5px;margin-bottom:5px}.multi-filter-list div{white-space:nowrap}.multi-filter-list .multi-filter-list-action{display:flex;justify-content:space-between;align-items:center}.multi-filter-list .and-or-button{margin:0 0 0 10px;min-width:58px;cursor:pointer}.multi-filter-list .trash-button{margin:0 0 -2px 6px;cursor:pointer;width:22px;height:100%;font-size:16px}.multi-filter-list .trash-button .trash-img{width:1em}.add-filter-divider{display:block;margin:0 -10px 10px -10px;border-bottom:1px solid var(--revo-grid-filter-panel-divider, #d9d9d9);height:10px}.select-input{display:flex;justify-content:space-between;align-items:center}";

const defaultType = 'none';
const FILTER_LIST_CLASS = 'multi-filter-list';
const FILTER_LIST_CLASS_ACTION = 'multi-filter-list-action';
const FILTER_ID = 'add-filter';
const FilterPanel = class {
    constructor(hostRef) {
        registerInstance(this, hostRef);
        this.filterChange = createEvent(this, "filterChange", 7);
        this.resetChange = createEvent(this, "resetChange", 7);
        this.filterCaptionsInternal = {
            title: 'Filter by',
            ok: 'Close',
            save: 'Save',
            // drops the filter
            reset: 'Reset',
            cancel: 'Cancel',
            add: 'Add condition',
            placeholder: 'Enter value...',
            and: 'and',
            or: 'or',
        };
        this.isFilterIdSet = false;
        this.filterId = 0;
        this.currentFilterId = -1;
        this.currentFilterType = defaultType;
        this.filterItems = {};
        this.filterNames = {};
        this.filterEntities = {};
        /**
         * Disables dynamic filtering. A way to apply filters on Save only
         */
        this.disableDynamicFiltering = false;
        /**
         * If true, closes the filter panel when clicking outside
         */
        this.closeOnOutsideClick = true;
        this.debouncedApplyFilter = debounce(() => {
            this.filterChange.emit(this.filterItems);
        }, 400);
    }
    onMouseDown(e) {
        // click on anything then select drops values to default
        if (!this.changes) {
            return;
        }
        const path = e.composedPath();
        const select = document.getElementById(FILTER_ID);
        if (select instanceof HTMLSelectElement) {
            // click on select should be skipped
            if (path.includes(select)) {
                return;
            }
            select.value = defaultType;
        }
        this.currentFilterType = defaultType;
        if (this.changes) {
            this.changes.type = defaultType;
        }
        this.currentFilterId = -1;
        const isOutside = !path.includes(this.element);
        if (e.target instanceof HTMLElement &&
            isOutside &&
            !isFilterBtn(e.target) &&
            this.closeOnOutsideClick) {
            this.changes = undefined;
        }
    }
    async show(newEntity) {
        this.changes = newEntity;
        this.filterItems = (newEntity === null || newEntity === void 0 ? void 0 : newEntity.filterItems) || {};
        if (this.changes) {
            this.changes.type = this.changes.type || defaultType;
        }
    }
    async getChanges() {
        return this.changes;
    }
    componentWillRender() {
        if (!this.isFilterIdSet) {
            this.isFilterIdSet = true;
            const filterItems = Object.keys(this.filterItems);
            for (const prop of filterItems) {
                // we set the proper filterId so there won't be any conflict when removing filters
                this.filterId += this.filterItems[prop].length;
            }
        }
    }
    getFilterItemsList() {
        var _a, _b;
        const prop = (_a = this.changes) === null || _a === void 0 ? void 0 : _a.prop;
        if (typeof prop === 'undefined')
            return '';
        const propFilters = (_b = this.filterItems[prop]) !== null && _b !== void 0 ? _b : [];
        const capts = Object.assign(this.filterCaptionsInternal, this.filterCaptions);
        return (h("div", { key: this.filterId }, propFilters.map((filter, index) => {
            let andOrButton;
            if (filter.hidden) {
                return;
            }
            // hide toggle button if there is only one filter and the last one
            if (index !== this.filterItems[prop].length - 1) {
                andOrButton = (h("div", { onClick: () => this.toggleFilterAndOr(filter.id) }, h(AndOrButton, { text: filter.relation === 'and' ? capts.and : capts.or })));
            }
            return (h("div", { key: filter.id, class: FILTER_LIST_CLASS }, h("div", { class: { 'select-input': true } }, h("select", { class: "select-css select-filter", onChange: e => this.onFilterTypeChange(e, prop, index) }, this.renderSelectOptions(this.filterItems[prop][index].type, true)), h("div", { class: FILTER_LIST_CLASS_ACTION }, andOrButton), h("div", { onClick: () => this.onRemoveFilter(filter.id) }, h(TrashButton, null))), h("div", null, this.renderExtra(prop, index))));
        }), propFilters.filter(f => !f.hidden).length > 0 ? h("div", { class: "add-filter-divider" }) : ''));
    }
    autoCorrect(el) {
        var _a, _b;
        if (!el) {
            return;
        }
        const revoGrid = el.closest('revo-grid');
        if (!revoGrid) {
            return;
        }
        const pos = el.getBoundingClientRect();
        const gridPos = revoGrid.getBoundingClientRect();
        const maxLeft = gridPos.right - pos.width;
        if (pos.left > maxLeft && el.offsetLeft) {
            el.style.left = `${maxLeft - ((_b = (_a = el.parentElement) === null || _a === void 0 ? void 0 : _a.getBoundingClientRect().left) !== null && _b !== void 0 ? _b : 0)}px`;
        }
    }
    onFilterTypeChange(e, prop, index) {
        if (!(e.target instanceof HTMLSelectElement)) {
            return;
        }
        this.filterItems[prop][index].type = e.target.value;
        // this re-renders the input to know if we need extra input
        this.filterId++;
        // adding setTimeout will wait for the next tick DOM update then focus on input
        setTimeout(() => {
            const input = document.getElementById('filter-input-' + this.filterItems[prop][index].id);
            if (input instanceof HTMLInputElement) {
                input.focus();
            }
        }, 0);
        if (!this.disableDynamicFiltering) {
            this.debouncedApplyFilter();
        }
    }
    onAddNewFilter(e) {
        const el = e.target;
        this.currentFilterType = el.value;
        this.addNewFilterToProp();
        // reset value after adding new filter
        const select = document.getElementById('add-filter');
        if (select) {
            select.value = defaultType;
            this.currentFilterType = defaultType;
        }
        if (!this.disableDynamicFiltering) {
            this.debouncedApplyFilter();
        }
    }
    addNewFilterToProp() {
        var _a;
        const prop = (_a = this.changes) === null || _a === void 0 ? void 0 : _a.prop;
        if (!(prop || prop === 0))
            return;
        if (!this.filterItems[prop]) {
            this.filterItems[prop] = [];
        }
        if (this.currentFilterType === 'none')
            return;
        this.filterId++;
        this.currentFilterId = this.filterId;
        this.filterItems[prop].push({
            id: this.currentFilterId,
            type: this.currentFilterType,
            value: '',
            relation: 'and',
        });
        // adding setTimeout will wait for the next tick DOM update then focus on input
        setTimeout(() => {
            const input = document.getElementById('filter-input-' + this.currentFilterId);
            if (input)
                input.focus();
        }, 0);
    }
    onSave() {
        this.filterChange.emit(this.filterItems);
    }
    onCancel() {
        this.changes = undefined;
    }
    onReset() {
        var _a;
        this.assertChanges();
        this.resetChange.emit((_a = this.changes) === null || _a === void 0 ? void 0 : _a.prop);
        // this updates the DOM which is used by getFilterItemsList() key
        this.filterId++;
    }
    onRemoveFilter(id) {
        var _a;
        this.assertChanges();
        // this is for reactivity issues for getFilterItemsList()
        this.filterId++;
        const prop = (_a = this.changes) === null || _a === void 0 ? void 0 : _a.prop;
        const items = this.filterItems[prop !== null && prop !== void 0 ? prop : ''];
        if (!items)
            return;
        const index = items.findIndex(d => d.id === id);
        if (index === -1)
            return;
        items.splice(index, 1);
        // let's remove the prop if no more filters so the filter icon will be removed
        if (items.length === 0)
            delete this.filterItems[prop !== null && prop !== void 0 ? prop : ''];
        if (!this.disableDynamicFiltering) {
            this.debouncedApplyFilter();
        }
    }
    toggleFilterAndOr(id) {
        var _a;
        this.assertChanges();
        // this is for reactivity issues for getFilterItemsList()
        this.filterId++;
        const prop = (_a = this.changes) === null || _a === void 0 ? void 0 : _a.prop;
        const items = this.filterItems[prop !== null && prop !== void 0 ? prop : ''];
        if (!items)
            return;
        const index = items.findIndex(d => d.id === id);
        if (index === -1)
            return;
        items[index].relation = items[index].relation === 'and' ? 'or' : 'and';
        if (!this.disableDynamicFiltering) {
            this.debouncedApplyFilter();
        }
    }
    assertChanges() {
        if (!this.changes) {
            throw new Error('Changes required per edit');
        }
    }
    renderSelectOptions(type, isDefaultTypeRemoved = false) {
        if (!this.changes) {
            return;
        }
        const options = [];
        const prop = this.changes.prop;
        const hidden = new Set();
        Object.entries(this.filterItems).forEach(([_, values]) => {
            values.forEach((filter) => {
                if (filter.hidden) {
                    hidden.add(filter.type);
                }
            });
        });
        if (!isDefaultTypeRemoved) {
            const capts = Object.assign(this.filterCaptionsInternal, this.filterCaptions);
            options.push(h("option", { selected: this.currentFilterType === defaultType, value: defaultType }, prop && this.filterItems[prop] && this.filterItems[prop].length > 0
                ? capts.add
                : this.filterNames[defaultType]));
        }
        for (let gIndex in this.changes.filterTypes) {
            const group = this.changes.filterTypes[gIndex].filter(k => !hidden.has(k));
            if (group.length) {
                options.push(...group.map(k => (h("option", { value: k, selected: type === k }, this.filterNames[k]))));
                options.push(h("option", { disabled: true }));
            }
        }
        return options;
    }
    renderExtra(prop, index) {
        const currentFilter = this.filterItems[prop];
        if (!currentFilter)
            return '';
        const applyFilter = (value) => {
            this.filterItems[prop][index].value = value;
            if (!this.disableDynamicFiltering) {
                this.debouncedApplyFilter();
            }
        };
        const focusNext = () => {
            const select = document.getElementById('add-filter');
            if (select) {
                select.value = defaultType;
                this.currentFilterType = defaultType;
                this.addNewFilterToProp();
                select.focus();
            }
        };
        const capts = Object.assign(this.filterCaptionsInternal, this.filterCaptions);
        const extra = this.filterEntities[currentFilter[index].type].extra;
        if (typeof extra === 'function') {
            return extra(h, {
                value: currentFilter[index].value,
                filter: currentFilter[index],
                prop,
                index,
                placeholder: capts.placeholder,
                onInput: (value) => {
                    applyFilter(value);
                },
                onFocus: () => {
                    focusNext();
                }
            });
        }
        if (extra !== 'input' && extra !== 'datepicker') {
            return '';
        }
        return (h("input", { id: `filter-input-${currentFilter[index].id}`, placeholder: capts.placeholder, type: extra === 'datepicker' ? 'date' : 'text', value: currentFilter[index].value, onInput: (e) => {
                if (e.target instanceof HTMLInputElement) {
                    applyFilter(e.target.value);
                }
            }, onKeyDown: e => {
                if (e.key.toLowerCase() === 'enter') {
                    const select = document.getElementById('add-filter');
                    if (select) {
                        focusNext();
                    }
                    return;
                }
                // keep event local, don't escalate farther to dom
                e.stopPropagation();
            } }));
    }
    render() {
        var _a, _b, _c;
        if (!this.changes) {
            return h(Host, { style: { display: 'none' } });
        }
        const style = {
            display: 'block',
            left: `${this.changes.x}px`,
            top: `${this.changes.y}px`,
        };
        const capts = Object.assign(this.filterCaptionsInternal, this.filterCaptions);
        return (h(Host, { style: style, ref: el => {
                var _a;
                ((_a = this.changes) === null || _a === void 0 ? void 0 : _a.autoCorrect) !== false && this.autoCorrect(el);
            } }, h("slot", { slot: "header" }), ((_b = (_a = this.changes).extraContent) === null || _b === void 0 ? void 0 : _b.call(_a, this.changes)) || '', ((_c = this.changes) === null || _c === void 0 ? void 0 : _c.hideDefaultFilters) !== true && ([
            h("label", null, capts.title),
            h("div", { class: "filter-holder" }, this.getFilterItemsList()),
            h("div", { class: "add-filter" }, h("select", { id: FILTER_ID, class: "select-css", onChange: e => this.onAddNewFilter(e) }, this.renderSelectOptions(this.currentFilterType)))
        ]), h("slot", null), h("div", { class: "filter-actions" }, this.disableDynamicFiltering && [
            h("button", { id: "revo-button-save", "aria-label": "save", class: "revo-button green", onClick: () => this.onSave() }, capts.save),
            h("button", { id: "revo-button-ok", "aria-label": "ok", class: "revo-button green", onClick: () => this.onCancel() }, capts.cancel),
        ], !this.disableDynamicFiltering && [
            h("button", { id: "revo-button-ok", "aria-label": "ok", class: "revo-button green", onClick: () => this.onCancel() }, capts.ok),
            h("button", { id: "revo-button-reset", "aria-label": "reset", class: "revo-button outline", onClick: () => this.onReset() }, capts.reset),
        ]), h("slot", { slot: "footer" })));
    }
    get element() { return getElement(this); }
};
FilterPanel.style = filterStyleCss;

export { FilterPanel as revogr_filter_panel };
//# sourceMappingURL=revogr-filter-panel.entry.esm.js.map

//# sourceMappingURL=revogr-filter-panel.entry.js.map