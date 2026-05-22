    const documentEpubExport = (() => {
      const encoder = new TextEncoder();
      let crcTable = null;

      function escapeEpubText(value) {
        return String(value ?? "")
          .replaceAll("&", "&amp;")
          .replaceAll("<", "&lt;")
          .replaceAll(">", "&gt;");
      }
      function escapeEpubAttribute(value) {
        return escapeEpubText(value).replaceAll('"', "&quot;");
      }
      function isSafeEpubUrl(value) {
        const text = String(value || "").trim();
        return Boolean(text) && !/^javascript:/i.test(text);
      }
      function textBytes(value) {
        return encoder.encode(String(value));
      }
      function cleanEpubId(value, fallback = "main-computer-document") {
        const cleaned = String(value || "").toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
        return cleaned || fallback;
      }
      function cleanEpubFilename(value) {
        return `${cleanEpubId(value, "main-computer-document")}.epub`;
      }
      function currentDocumentTitle() {
        const firstHeading = documentCanvas?.querySelector?.(".mc-page-content h1, .mc-page-content h2, .mc-page-content h3");
        const headingText = firstHeading?.textContent?.trim();
        if (headingText) return headingText.slice(0, 120);
        const pathText = documentCurrentPath?.textContent?.trim();
        if (pathText && pathText !== "local draft") return pathText.split("/").pop().replace(/\.[^.]+$/, "").slice(0, 120);
        return "Main Computer Document";
      }
      function cloneDocumentContentForExport() {
        const root = document.createElement("div");
        getDocumentPageContents().forEach((content) => {
          const clone = content.cloneNode(true);
          if (typeof prepareDocumentObjectsForSerialization === "function") {
            prepareDocumentObjectsForSerialization(clone);
          }
          while (clone.firstChild) root.appendChild(clone.firstChild);
        });
        root.querySelectorAll("[data-document-caret-marker], .document-plugin-anchor-highlight").forEach((element) => element.remove());
        return root;
      }
      function mathObjectToEpub(element) {
        const layout = element.dataset.docObjectLayout === "paragraph" ? "paragraph" : "inline";
        const latex = element.dataset.latex || element.textContent || "";
        if (layout === "paragraph") {
          return `<p class="math math-block">${escapeEpubText(latex)}</p>`;
        }
        return `<span class="math math-inline">${escapeEpubText(latex)}</span>`;
      }
      function serializeEpubChildren(element) {
        return Array.from(element.childNodes).map(serializeEpubNode).join("");
      }
      function serializeEpubNode(node) {
        if (node.nodeType === Node.TEXT_NODE) return escapeEpubText(node.nodeValue || "");
        if (node.nodeType !== Node.ELEMENT_NODE) return "";
        const element = node;
        if (element.dataset?.docObject === "math") return mathObjectToEpub(element);
        const tag = element.tagName.toLowerCase();
        if (tag === "br") return "<br />";
        if (tag === "img") {
          const src = element.getAttribute("src") || "";
          if (!isSafeEpubUrl(src)) return "";
          const alt = element.getAttribute("alt") || "";
          return `<img src="${escapeEpubAttribute(src)}" alt="${escapeEpubAttribute(alt)}" />`;
        }
        const allowed = new Set(["a", "b", "blockquote", "code", "div", "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "i", "li", "mark", "ol", "p", "pre", "small", "span", "strong", "sub", "sup", "u", "ul"]);
        const normalizedTag = allowed.has(tag) ? tag : "span";
        const attrs = [];
        if (normalizedTag === "a") {
          const href = element.getAttribute("href") || "";
          if (isSafeEpubUrl(href)) attrs.push(` href="${escapeEpubAttribute(href)}"`);
        }
        if (normalizedTag === "span" && element.classList.contains("document-math-body")) {
          attrs.push(' class="math math-inline"');
        }
        const content = serializeEpubChildren(element);
        if (!content && ["p", "div", "blockquote"].includes(normalizedTag)) return `<${normalizedTag}><br /></${normalizedTag}>`;
        return `<${normalizedTag}${attrs.join("")}>${content}</${normalizedTag}>`;
      }
      function documentContentToXhtmlBody() {
        const root = cloneDocumentContentForExport();
        const body = serializeEpubChildren(root).trim();
        return body || "<p></p>";
      }
      function epubStylesheet() {
        return [
          "body { font-family: Arial, Helvetica, sans-serif; line-height: 1.55; margin: 1.5em; }",
          "h1, h2, h3 { line-height: 1.2; }",
          ".math { font-family: Georgia, 'Times New Roman', serif; }",
          ".math-block { text-align: center; margin: 1em 0; }",
          "img { max-width: 100%; height: auto; }",
          "pre { white-space: pre-wrap; }"
        ].join("\n");
      }
      function packageOpf(title, identifier) {
        return `<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="book-id" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">${escapeEpubText(identifier)}</dc:identifier>
    <dc:title>${escapeEpubText(title)}</dc:title>
    <dc:language>en</dc:language>
    <meta property="dcterms:modified">${new Date().toISOString().replace(/\.\d{3}Z$/, "Z")}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />
    <item id="chapter-1" href="chapter-1.xhtml" media-type="application/xhtml+xml" />
    <item id="styles" href="styles.css" media-type="text/css" />
  </manifest>
  <spine>
    <itemref idref="chapter-1" />
  </spine>
</package>`;
      }
      function navXhtml(title) {
        return `<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
  <head>
    <title>${escapeEpubText(title)} Navigation</title>
  </head>
  <body>
    <nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops">
      <h1>${escapeEpubText(title)}</h1>
      <ol>
        <li><a href="chapter-1.xhtml">${escapeEpubText(title)}</a></li>
      </ol>
    </nav>
  </body>
</html>`;
      }
      function chapterXhtml(title, body) {
        return `<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
  <head>
    <title>${escapeEpubText(title)}</title>
    <link rel="stylesheet" type="text/css" href="styles.css" />
  </head>
  <body>
${body}
  </body>
</html>`;
      }
      function containerXml() {
        return `<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/package.opf" media-type="application/oebps-package+xml" />
  </rootfiles>
</container>`;
      }
      function ensureCrcTable() {
        if (crcTable) return crcTable;
        crcTable = new Uint32Array(256);
        for (let index = 0; index < 256; index += 1) {
          let c = index;
          for (let bit = 0; bit < 8; bit += 1) {
            c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
          }
          crcTable[index] = c >>> 0;
        }
        return crcTable;
      }
      function crc32(bytes) {
        const table = ensureCrcTable();
        let crc = 0xffffffff;
        for (let index = 0; index < bytes.length; index += 1) {
          crc = table[(crc ^ bytes[index]) & 0xff] ^ (crc >>> 8);
        }
        return (crc ^ 0xffffffff) >>> 0;
      }
      function dosDateTime(date = new Date()) {
        const year = Math.max(1980, date.getFullYear());
        return {
          time: (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2),
          date: ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate()
        };
      }
      function headerBytes(size, writer) {
        const bytes = new Uint8Array(size);
        const view = new DataView(bytes.buffer);
        writer(view);
        return bytes;
      }
      function makeStoredZip(entries) {
        const chunks = [];
        const central = [];
        let offset = 0;
        const stamp = dosDateTime();
        entries.forEach((entry) => {
          const name = textBytes(entry.path);
          const data = entry.bytes instanceof Uint8Array ? entry.bytes : textBytes(entry.bytes);
          const crc = crc32(data);
          const local = headerBytes(30, (view) => {
            view.setUint32(0, 0x04034b50, true);
            view.setUint16(4, 20, true);
            view.setUint16(6, 0, true);
            view.setUint16(8, 0, true);
            view.setUint16(10, stamp.time, true);
            view.setUint16(12, stamp.date, true);
            view.setUint32(14, crc, true);
            view.setUint32(18, data.length, true);
            view.setUint32(22, data.length, true);
            view.setUint16(26, name.length, true);
            view.setUint16(28, 0, true);
          });
          chunks.push(local, name, data);
          const centralHeader = headerBytes(46, (view) => {
            view.setUint32(0, 0x02014b50, true);
            view.setUint16(4, 20, true);
            view.setUint16(6, 20, true);
            view.setUint16(8, 0, true);
            view.setUint16(10, 0, true);
            view.setUint16(12, stamp.time, true);
            view.setUint16(14, stamp.date, true);
            view.setUint32(16, crc, true);
            view.setUint32(20, data.length, true);
            view.setUint32(24, data.length, true);
            view.setUint16(28, name.length, true);
            view.setUint16(30, 0, true);
            view.setUint16(32, 0, true);
            view.setUint16(34, 0, true);
            view.setUint16(36, 0, true);
            view.setUint32(38, 0, true);
            view.setUint32(42, offset, true);
          });
          central.push(centralHeader, name);
          offset += local.length + name.length + data.length;
        });
        const centralOffset = offset;
        let centralSize = 0;
        central.forEach((chunk) => { centralSize += chunk.length; });
        const end = headerBytes(22, (view) => {
          view.setUint32(0, 0x06054b50, true);
          view.setUint16(4, 0, true);
          view.setUint16(6, 0, true);
          view.setUint16(8, entries.length, true);
          view.setUint16(10, entries.length, true);
          view.setUint32(12, centralSize, true);
          view.setUint32(16, centralOffset, true);
          view.setUint16(20, 0, true);
        });
        return new Blob([...chunks, ...central, end], {type: "application/epub+zip"});
      }
      function buildEpubBlob() {
        const title = currentDocumentTitle();
        const identifier = `urn:uuid:${window.crypto?.randomUUID?.() || cleanEpubId(title)}`;
        const body = documentContentToXhtmlBody();
        const entries = [
          {path: "mimetype", bytes: textBytes("application/epub+zip")},
          {path: "META-INF/container.xml", bytes: textBytes(containerXml())},
          {path: "OEBPS/package.opf", bytes: textBytes(packageOpf(title, identifier))},
          {path: "OEBPS/nav.xhtml", bytes: textBytes(navXhtml(title))},
          {path: "OEBPS/chapter-1.xhtml", bytes: textBytes(chapterXhtml(title, body))},
          {path: "OEBPS/styles.css", bytes: textBytes(epubStylesheet())}
        ];
        return {title, blob: makeStoredZip(entries)};
      }
      function downloadBlob(blob, filename) {
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = filename;
        anchor.rel = "noopener";
        document.body.append(anchor);
        anchor.click();
        anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1500);
      }
      function exportCurrentDocumentAsEpub() {
        try {
          const {title, blob} = buildEpubBlob();
          downloadBlob(blob, cleanEpubFilename(title));
          if (documentStatus) documentStatus.textContent = "EPUB exported";
        } catch (error) {
          if (documentStatus) documentStatus.textContent = `EPUB export failed: ${error.message || error}`;
          throw error;
        }
      }
      return {buildEpubBlob, exportCurrentDocumentAsEpub};
    })();

    function exportCurrentDocumentAsEpub() {
      documentEpubExport.exportCurrentDocumentAsEpub();
    }
