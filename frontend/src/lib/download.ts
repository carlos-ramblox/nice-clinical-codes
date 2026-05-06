// Firefox ignores the `download` attribute on anchors that never
// enter the DOM, saving the file under the Blob URL's GUID instead.
// Appending to document.body before click() avoids that.
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Filesystem-safe slug for export filenames. Lowercases, collapses any
// run of non-alphanumeric characters to a single hyphen, trims leading
// and trailing hyphens, and caps the length so we don't hit Windows'
// 260-char path limit. Empty / whitespace-only input returns ``fallback``
// so the user never sees a download named ``.csv``. Used by every
// codelist export (CSV / OHDSI on /gallery, /codelists, /).
export function slugify(name: string, fallback = "codelist", maxLen = 60): string {
  const slug = (name || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, maxLen);
  return slug || fallback;
}
