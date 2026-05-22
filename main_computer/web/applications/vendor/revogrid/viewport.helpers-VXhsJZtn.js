/*!
 * Built by Revolist OU ❤️
 */
/**
 * Collects data for pinned columns in the required @ViewportProps format.
 */
/**
 * Represents the slot names for the viewport slots.
 */
const HEADER_SLOT = 'header'; // Slot name for the header slot
const FOOTER_SLOT = 'footer'; // Slot name for the footer slot
const CONTENT_SLOT = 'content'; // Slot name for the content slot
const DATA_SLOT = 'data'; // Slot name for the data slot
/**
 * Returns the last visible cell in the viewport for a given row type.
 * Coordinates are not zero-based and are relative to the viewport.
 * If needed to be zero-based they can be adjusted by subtracting 1.
 */
function getLastCell(data, rowType) {
    // Get the last visible column count from the viewport column data.
    const lastVisibleColumnCount = data.viewports[data.colType].store.get('realCount');
    // Get the last visible row count for the given row type from the viewport column data.
    const lastVisibleRowCount = data.viewports[rowType].store.get('realCount');
    // Return the last visible cell with the last visible column count and row count.
    return {
        x: lastVisibleColumnCount,
        y: lastVisibleRowCount,
    };
}
function viewportDataPartition(data, type, slot, fixed) {
    return {
        colData: data.colStore,
        viewportCol: data.viewports[data.colType].store,
        viewportRow: data.viewports[type].store,
        /**
         * lastCell is the last real coordinate + 1, saved to selection store
         */
        lastCell: getLastCell(data, type),
        slot,
        type,
        canDrag: !fixed,
        position: data.position,
        dataStore: data.rowStores[type].store,
        dimensionCol: data.dimensions[data.colType].store,
        dimensionRow: data.dimensions[type].store,
        style: fixed
            ? { height: `${data.dimensions[type].store.get('realSize')}px` }
            : undefined,
    };
}

export { CONTENT_SLOT as C, DATA_SLOT as D, FOOTER_SLOT as F, HEADER_SLOT as H, viewportDataPartition as v };
//# sourceMappingURL=viewport.helpers-VXhsJZtn.js.map

//# sourceMappingURL=viewport.helpers-VXhsJZtn.js.map