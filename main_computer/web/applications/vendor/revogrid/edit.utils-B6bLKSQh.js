/*!
 * Built by Revolist OU ❤️
 */
import { a1 as KeyCodesEnum, a2 as OsPlatform, U as codesLetter, V as keyValues, N as EDIT_INPUT_WR } from './dimension.helpers-D5lwLPzd.js';

function isMetaKey(code) {
    const keys = [
        KeyCodesEnum.ARROW_DOWN,
        KeyCodesEnum.ARROW_UP,
        KeyCodesEnum.ARROW_LEFT,
        KeyCodesEnum.ARROW_RIGHT,
        KeyCodesEnum.HOME,
        KeyCodesEnum.END,
        KeyCodesEnum.DELETE,
        KeyCodesEnum.BACKSPACE,
        KeyCodesEnum.F1,
        KeyCodesEnum.F2,
        KeyCodesEnum.F3,
        KeyCodesEnum.F4,
        KeyCodesEnum.F5,
        KeyCodesEnum.F6,
        KeyCodesEnum.F7,
        KeyCodesEnum.F8,
        KeyCodesEnum.F9,
        KeyCodesEnum.F10,
        KeyCodesEnum.F11,
        KeyCodesEnum.F12,
        KeyCodesEnum.TAB,
        KeyCodesEnum.PAGE_DOWN,
        KeyCodesEnum.PAGE_UP,
        KeyCodesEnum.ENTER,
        KeyCodesEnum.ESCAPE,
        KeyCodesEnum.SHIFT,
        KeyCodesEnum.CAPS_LOCK,
        KeyCodesEnum.ALT,
    ];
    return keys.indexOf(code) !== -1;
}
// navigator.platform
function isCtrlKey(code, platform) {
    if (platform.includes(OsPlatform.mac)) {
        return [
            KeyCodesEnum.COMMAND_LEFT,
            KeyCodesEnum.COMMAND_RIGHT,
            KeyCodesEnum.COMMAND_FIREFOX,
        ].includes(code);
    }
    return code === KeyCodesEnum.CONTROL;
}
function isCtrlMetaKey(code) {
    return [
        KeyCodesEnum.CONTROL,
        KeyCodesEnum.COMMAND_LEFT,
        KeyCodesEnum.COMMAND_RIGHT,
        KeyCodesEnum.COMMAND_FIREFOX,
    ].includes(code);
}
function isClear(code) {
    return codesLetter.BACKSPACE === code || codesLetter.DELETE === code;
}
function isTab(code) {
    return codesLetter.TAB === code;
}
function isTabKeyValue(key) {
    return keyValues.TAB === key;
}
function isEnterKeyValue(key) {
    return keyValues.ENTER === key;
}
function isCut(event) {
    return ((event.ctrlKey && event.code === 'KeyX') || // Ctrl + X on Windows
        (event.metaKey && event.code === 'KeyX')); // Cmd + X on Mac
}
function isCopy(event) {
    return ((event.ctrlKey && event.code === 'KeyC') || // Ctrl + C on Windows
        (event.metaKey && event.code === 'KeyC')); // Cmd + C on Mac
}
function isPaste(event) {
    return ((event.ctrlKey && event.code === 'KeyV') || // Ctrl + V on Windows
        (event.metaKey && event.code === 'KeyV')); // Cmd + V on Mac
}
function isAll(event) {
    return ((event.ctrlKey && event.code === 'KeyA') || // Ctrl + A on Windows
        (event.metaKey && event.code === 'KeyA')); // Cmd + A on Mac
}

// is edit input
function isEditInput(el) {
    return !!(el === null || el === void 0 ? void 0 : el.closest(`.${EDIT_INPUT_WR}`));
}
// Type guard for EditorCtrConstructible
function isEditorCtrConstructible(editor) {
    return typeof editor === 'function' && typeof editor.prototype === 'object';
}

export { isCtrlKey as a, isCtrlMetaKey as b, isClear as c, isTab as d, isTabKeyValue as e, isEnterKeyValue as f, isCut as g, isCopy as h, isMetaKey as i, isPaste as j, isAll as k, isEditInput as l, isEditorCtrConstructible as m };
//# sourceMappingURL=edit.utils-B6bLKSQh.js.map

//# sourceMappingURL=edit.utils-B6bLKSQh.js.map