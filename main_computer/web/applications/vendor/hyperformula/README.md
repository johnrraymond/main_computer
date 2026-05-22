# Local HyperFormula vendor asset

`hyperformula.full.min.js` is a same-origin browser asset used by the Main
Computer spreadsheet app before the CDN fallback.

This repository snapshot could not include an upstream package-manager install
step, so the checked-in file is a small HyperFormula-compatible local fallback
that implements the browser API surface the spreadsheet app currently calls and
covers offline smoke/basic formulas such as `=A1+B1` and common aggregate/text
functions. The loader still keeps the jsDelivr HyperFormula URL as the backup
source for the full upstream distribution when replacing this fallback with the
official build.

Replace this file with the upstream `hyperformula@3.2.0/dist/hyperformula.full.min.js`
browser build when the project is ready to vendor the full dependency.
