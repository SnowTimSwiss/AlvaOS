# noVNC (vendored)

The VNC client that shows a virtual machine's screen in the browser
(`vms.js`). It is [noVNC](https://github.com/novnc/noVNC) 1.7.0, MPL-2.0
(`LICENSE.txt`), with its `pako` inflate code (MIT, `LICENSE-pako.txt`),
bundled into one file so the web UI loads nothing from outside.

`novnc.js` is made from the package `@novnc/novnc@1.7.0` and sets `window.RFB`:

    echo "import RFB from './node_modules/@novnc/novnc/core/rfb.js'; window.RFB = RFB;" > entry.js
    npm i @novnc/novnc@1.7.0 esbuild
    esbuild entry.js --bundle --minify --format=esm --target=es2022 --legal-comments=none --outfile=novnc.js

Do not edit `novnc.js`; update by building it again.
