/*!
 * Built by Revolist OU ❤️
 */
export { o as GROUPING_ROW_TYPE, j as GROUP_COLUMN_PROP, G as GROUP_DEPTH, h as GROUP_EXPANDED, l as GROUP_EXPAND_BTN, m as GROUP_EXPAND_EVENT, k as GROUP_ORIGINAL_INDEX, f as PSEUDO_GROUP_COLUMN, P as PSEUDO_GROUP_ITEM, d as PSEUDO_GROUP_ITEM_ID, e as PSEUDO_GROUP_ITEM_VALUE, c as columnTypes, a as cropCellToMax, H as gatherGroup, s as gatherGrouping, z as getCellData, B as getCellDataParsed, A as getCellRaw, I as getColumnByProp, D as getColumnSizes, C as getColumnType, F as getColumns, q as getExpanded, t as getGroupingName, x as getParsedGroup, g as getRange, p as getSource, E as isColGrouping, u as isGrouping, v as isGroupingColumn, b as isRangeSingleCell, i as isRowType, y as isSameGroup, w as measureEqualDepth, n as nextCell, r as rowTypes } from './column.service-DT_CqxqZ.js';
import { B as BasePlugin } from './column.drag.plugin-CmcURdIJ.js';
export { A as AutoSizeColumnPlugin, C as ColumnMovePlugin, D as DimensionStore, b as ExportCsv, E as ExportFilePlugin, c as FILTER_CONFIG_CHANGED_EVENT, F as FILTER_TRIMMED_TYPE, d as FILTE_PANEL, e as FilterPlugin, G as GroupingRowPlugin, S as SelectionStore, m as SortingPlugin, a as StretchColumn, n as defaultCellCompare, o as descCellCompare, j as doCollapse, k as doExpand, f as filterCoreFunctionsIndexedByType, h as filterNames, g as filterTypes, q as getComparer, l as getLeftRelative, p as getNextOrder, i as isStretchPlugin, s as sortIndexByItems } from './column.drag.plugin-CmcURdIJ.js';
export { d as dispatch, a as dispatchByEvent } from './header-cell-renderer-DNIoql0s.js';
export { C as CellRenderer, G as GroupingRowRenderer, S as SortingSign, e as expandEvent, a as expandSvgIconVNode } from './cell-renderer-ePazz-Zt.js';
export { C as CELL_CLASS, L as CELL_HANDLER_CLASS, x as DATA_COL, y as DATA_ROW, z as DISABLED_CLASS, I as DRAGGABLE_CLASS, O as DRAGG_TEXT, G as DRAG_ICON_CLASS, D as DataStore, N as EDIT_INPUT_WR, J as FOCUS_CLASS, P as GRID_INTERNALS, F as HEADER_ACTUAL_ROW_CLASS, H as HEADER_CLASS, E as HEADER_ROW_CLASS, B as HEADER_SORTABLE_CLASS, M as MIN_COL_SIZE, K as MOBILE_CLASS, R as RESIZE_INTERVAL, Q as ROW_FOCUSED_CLASS, A as ROW_HEADER_TYPE, S as SELECTION_BORDER_CLASS, T as TMP_SELECTION_BG_CLASS, v as applyMixins, i as calculateDimensionData, U as codesLetter, l as findPositionInArray, h as gatherTrimmedItems, k as getItemByIndex, j as getItemByPosition, g as getPhysical, o as getScrollbarSize, b as getSourceItem, f as getSourceItemVirtualIndexByProp, c as getSourcePhysicalIndex, a as getVisibleSourceItem, V as keyValues, n as mergeSortedArray, p as proxyPlugin, m as pushSorted, r as range, q as scaleValue, e as setItems, d as setSourceByPhysicalIndex, s as setSourceByVirtualIndex, w as setStore, u as timeout, t as trimmedPlugin } from './dimension.helpers-D5lwLPzd.js';
export { T as TextEditor } from './text-editor-DlCRmV75.js';
export { k as isAll, c as isClear, h as isCopy, a as isCtrlKey, b as isCtrlMetaKey, g as isCut, l as isEditInput, m as isEditorCtrConstructible, f as isEnterKeyValue, i as isMetaKey, j as isPaste, d as isTab, e as isTabKeyValue } from './edit.utils-B6bLKSQh.js';
export { h } from './index-Dyptvvxf.js';
export { V as ViewportStore, a as addMissingItems, f as calculateRowHeaderSize, d as getFirstItem, b as getItems, e as getLastItem, g as getUpdatedItemsByPosition, i as isActiveRange, c as isActiveRangeOutsideLastItem, r as recombineByOffset, s as setItemSizes, u as updateMissingAndRange } from './viewport.store-2mQugd8S.js';
export { A as AND_OR_BUTTON, e as AndOrButton, a as FILTER_BUTTON_ACTIVE, F as FILTER_BUTTON_CLASS, b as FILTER_PROP, c as FilterButton, T as TRASH_BUTTON, d as TrashButton, i as isFilterBtn } from './filter.button-DmOE7VCJ.js';
import './debounce-BfO9dz9v.js';

const REVOGRID_EVENTS = new Map([
    ['contentsizechanged', 'contentsizechanged'],
    ['beforeedit', 'beforeedit'],
    ['beforerangeedit', 'beforerangeedit'],
    ['afteredit', 'afteredit'],
    ['beforeautofill', 'beforeautofill'],
    ['beforerange', 'beforerange'],
    ['afterfocus', 'afterfocus'],
    ['roworderchanged', 'roworderchanged'],
    ['beforesorting', 'beforesorting'],
    ['beforesourcesortingapply', 'beforesourcesortingapply'],
    ['beforesortingapply', 'beforesortingapply'],
    ['rowdragstart', 'rowdragstart'],
    ['headerclick', 'headerclick'],
    ['beforecellfocus', 'beforecellfocus'],
    ['beforefocuslost', 'beforefocuslost'],
    ['beforesourceset', 'beforesourceset'],
    ['beforeanysource', 'beforeanysource'],
    ['aftersourceset', 'aftersourceset'],
    ['afteranysource', 'afteranysource'],
    ['beforecolumnsset', 'beforecolumnsset'],
    ['beforecolumnapplied', 'beforecolumnapplied'],
    ['aftercolumnsset', 'aftercolumnsset'],
    ['beforefilterapply', 'beforefilterapply'],
    ['beforefiltertrimmed', 'beforefiltertrimmed'],
    ['beforetrimmed', 'beforetrimmed'],
    ['aftertrimmed', 'aftertrimmed'],
    ['viewportscroll', 'viewportscroll'],
    ['beforeexport', 'beforeexport'],
    ['beforeeditstart', 'beforeeditstart'],
    ['aftercolumnresize', 'aftercolumnresize'],
    ['beforerowdefinition', 'beforerowdefinition'],
    ['filterconfigchanged', 'filterconfigchanged'],
    ['sortingconfigchanged', 'sortingconfigchanged'],
    ['rowheaderschanged', 'rowheaderschanged'],
    ['beforegridrender', 'beforegridrender'],
    ['aftergridrender', 'aftergridrender'],
    ['aftergridinit', 'aftergridinit'],
    ['additionaldatachanged', 'additionaldatachanged'],
    ['afterthemechanged', 'afterthemechanged'],
    ['created', 'created'],
    ['beforepaste', 'beforepaste'],
    ['beforepasteapply', 'beforepasteapply'],
    ['pasteregion', 'pasteregion'],
    ['afterpasteapply', 'afterpasteapply'],
    ['beforecut', 'beforecut'],
    ['clearregion', 'clearregion'],
    ['beforecopy', 'beforecopy'],
    ['beforecopyapply', 'beforecopyapply'],
    ['copyregion', 'copyregion'],
    ['beforerowrender', 'beforerowrender'],
    ['afterrender', 'afterrender'],
    ['beforecellrender', 'beforecellrender'],
    ['beforedatarender', 'beforedatarender'],
    ['dragstartcell', 'dragstartcell'],
    ['celleditinit', 'celleditinit'],
    ['closeedit', 'closeedit'],
    ['filterChange', 'filterChange'],
    ['resetChange', 'resetChange'],
    ['beforefocusrender', 'beforefocusrender'],
    ['beforescrollintoview', 'beforescrollintoview'],
    ['afterfocus', 'afterfocus'],
    ['beforeheaderclick', 'beforeheaderclick'],
    ['headerresize', 'headerresize'],
    ['beforeheaderresize', 'beforeheaderresize'],
    ['headerdblclick', 'headerdblclick'],
    ['beforeheaderrender', 'beforeheaderrender'],
    ['beforegroupheaderrender', 'beforegroupheaderrender'],
    ['afterheaderrender', 'afterheaderrender'],
    ['rowdragstartinit', 'rowdragstartinit'],
    ['rowdragendinit', 'rowdragendinit'],
    ['rowdragmoveinit', 'rowdragmoveinit'],
    ['rowdragmousemove', 'rowdragmousemove'],
    ['rowdropinit', 'rowdropinit'],
    ['roworderchange', 'roworderchange'],
    ['beforecopyregion', 'beforecopyregion'],
    ['beforepasteregion', 'beforepasteregion'],
    ['celleditapply', 'celleditapply'],
    ['beforecellfocusinit', 'beforecellfocusinit'],
    ['beforenextvpfocus', 'beforenextvpfocus'],
    ['setedit', 'setedit'],
    ['beforeapplyrange', 'beforeapplyrange'],
    ['beforesetrange', 'beforesetrange'],
    ['setrange', 'setrange'],
    ['beforeeditrender', 'beforeeditrender'],
    ['selectall', 'selectall'],
    ['canceledit', 'canceledit'],
    ['settemprange', 'settemprange'],
    ['beforesettemprange', 'beforesettemprange'],
    ['applyfocus', 'applyfocus'],
    ['focuscell', 'focuscell'],
    ['beforerangedataapply', 'beforerangedataapply'],
    ['selectionchangeinit', 'selectionchangeinit'],
    ['beforerangecopyapply', 'beforerangecopyapply'],
    ['rangeeditapply', 'rangeeditapply'],
    ['clipboardrangecopy', 'clipboardrangecopy'],
    ['clipboardrangepaste', 'clipboardrangepaste'],
    ['beforekeydown', 'beforekeydown'],
    ['beforekeyup', 'beforekeyup'],
    ['beforecellsave', 'beforecellsave'],
    ['celledit', 'celledit'],
    ['scrollview', 'scrollview'],
    ['ref', 'ref'],
    ['scrollvirtual', 'scrollvirtual'],
    ['scrollviewport', 'scrollviewport'],
    ['resizeviewport', 'resizeviewport'],
    ['scrollchange', 'scrollchange'],
    ['scrollviewportsilent', 'scrollviewportsilent'],
    ['html', 'html']
]);

/**
 * Automatically adds new rows when pasted data is larger than current rows
 * @event newRows - is triggered when new rows are added. Data of new rows can be filled with default values. If the event is prevented, no rows will be added
 */
class AutoAddRowsPlugin extends BasePlugin {
    constructor(revogrid, providers) {
        super(revogrid, providers);
        this.addEventListener('beforepasteapply', evt => this.handleBeforePasteApply(evt));
    }
    handleBeforePasteApply(event) {
        const start = this.providers.selection.focused;
        const isEditing = this.providers.selection.edit != null;
        if (!start || isEditing) {
            return;
        }
        const rowLength = this.providers.data.stores.rgRow.store.get('items').length;
        const endRow = start.y + event.detail.parsed.length;
        if (rowLength < endRow) {
            const count = endRow - rowLength;
            const newRows = Array.from({ length: count }, (_, i) => ({
                index: rowLength + i,
                data: {},
            }));
            const event = this.emit('newRows', { newRows: newRows });
            if (event.defaultPrevented) {
                return;
            }
            const items = [
                ...this.providers.data.stores.rgRow.store.get('source'),
                ...event.detail.newRows.map(j => j.data),
            ];
            this.providers.data.setData(items);
        }
    }
}

export { AutoAddRowsPlugin, BasePlugin, REVOGRID_EVENTS };
//# sourceMappingURL=index.esm.js.map

//# sourceMappingURL=index.esm.js.map