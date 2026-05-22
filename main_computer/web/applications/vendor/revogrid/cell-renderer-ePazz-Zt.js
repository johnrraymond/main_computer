/*!
 * Built by Revolist OU ❤️
 */
import { h, B as Build } from './index-Dyptvvxf.js';
import { y as DATA_ROW, G as DRAG_ICON_CLASS, I as DRAGGABLE_CLASS } from './dimension.helpers-D5lwLPzd.js';
import { l as GROUP_EXPAND_BTN, m as GROUP_EXPAND_EVENT, G as GROUP_DEPTH, h as GROUP_EXPANDED, P as PSEUDO_GROUP_ITEM, O as isRowDragService, B as getCellDataParsed } from './column.service-DT_CqxqZ.js';

const SortingSign = ({ column }) => {
    var _a;
    return h("i", { class: (_a = column === null || column === void 0 ? void 0 : column.order) !== null && _a !== void 0 ? _a : 'sort-off' });
};

const PADDING_DEPTH = 10;
const RowRenderer = ({ rowClass, index, size, start, depth }, cells) => {
    const props = Object.assign({ [DATA_ROW]: index });
    return (h("div", Object.assign({}, props, { class: `rgRow ${rowClass || ''}`, style: {
            height: `${size}px`,
            transform: `translateY(${start}px)`,
            paddingLeft: depth ? `${PADDING_DEPTH * depth}px` : undefined,
        } }), cells));
};

function expandEvent(e, model, virtualIndex) {
    var _a;
    const event = new CustomEvent(GROUP_EXPAND_EVENT, {
        detail: {
            model,
            virtualIndex,
        },
        cancelable: true,
        bubbles: true,
    });
    (_a = e.target) === null || _a === void 0 ? void 0 : _a.dispatchEvent(event);
}
const GroupingRowRenderer = (props) => {
    const { model, itemIndex, hasExpand, groupingCustomRenderer } = props;
    const name = model[PSEUDO_GROUP_ITEM];
    const expanded = model[GROUP_EXPANDED];
    const depth = parseInt(model[GROUP_DEPTH], 10) || 0;
    if (groupingCustomRenderer) {
        return (h(RowRenderer, Object.assign({}, props, { rowClass: "groupingRow", depth: depth }),
            h("div", { onClick: e => expandEvent(e, model, itemIndex) }, groupingCustomRenderer(h, Object.assign(Object.assign({}, props), { colType: props.providers.colType, name,
                expanded,
                depth })))));
    }
    return (h(RowRenderer, Object.assign({}, props, { rowClass: "groupingRow", depth: depth }), hasExpand && [
        h("button", { class: { [GROUP_EXPAND_BTN]: true }, onClick: e => expandEvent(e, model, itemIndex) }, expandSvgIconVNode(expanded)),
        name,
    ]));
};
const expandSvgIconVNode = (expanded = false) => {
    return (h("svg", { "aria-hidden": "true", style: { transform: `rotate(${!expanded ? -90 : 0}deg)` }, focusable: "false", viewBox: "0 0 448 512" },
        h("path", { fill: "currentColor", d: "M207.029 381.476L12.686 187.132c-9.373-9.373-9.373-24.569 0-33.941l22.667-22.667c9.357-9.357 24.522-9.375 33.901-.04L224 284.505l154.745-154.021c9.379-9.335 24.544-9.317 33.901.04l22.667 22.667c9.373 9.373 9.373 24.569 0 33.941L240.971 381.476c-9.373 9.372-24.569 9.372-33.942 0z" })));
};

function renderCell(v) {
    var _a;
    const els = [];
    // #region Custom cell
    const template = (_a = v.schemaModel.column) === null || _a === void 0 ? void 0 : _a.cellTemplate;
    if (template) {
        els.push(template(h, v.schemaModel, v.additionalData));
    }
    // #endregion
    // #region Regular cell
    else {
        if (!v.schemaModel.column) {
            // something is wrong with data
            if (Build === null || Build === void 0 ? void 0 : Build.isDev) {
                console.error('Investigate column problem.', v.schemaModel);
            }
            return '';
        }
        // Row drag
        if (v.schemaModel.column.rowDrag &&
            isRowDragService(v.schemaModel.column.rowDrag, v.schemaModel)) {
            els.push(h("span", { class: DRAGGABLE_CLASS, onMouseDown: originalEvent => {
                    var _a;
                    return (_a = v.dragStartCell) === null || _a === void 0 ? void 0 : _a.emit({
                        originalEvent,
                        model: v.schemaModel,
                    });
                } },
                h("span", { class: DRAG_ICON_CLASS })));
        }
        els.push(`${getCellDataParsed(v.schemaModel.model, v.schemaModel.column)}`);
    }
    return els;
}
const CellRenderer = ({ renderProps, cellProps, }) => {
    const render = renderCell.bind(null, renderProps);
    return (h("div", Object.assign({}, cellProps, { redraw: render }), render()));
};

export { CellRenderer as C, GroupingRowRenderer as G, PADDING_DEPTH as P, RowRenderer as R, SortingSign as S, expandSvgIconVNode as a, expandEvent as e };
//# sourceMappingURL=cell-renderer-ePazz-Zt.js.map

//# sourceMappingURL=cell-renderer-ePazz-Zt.js.map