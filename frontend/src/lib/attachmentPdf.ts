/** 只读取本地页数。Worker 无法启动时由上传服务校验文件。 */
export async function validatePdfPages(file: File, maxPages: number, signal: AbortSignal): Promise<string | null> {
  try {
    const [pdf, worker, data] = await Promise.all([
      import("pdfjs-dist"),
      import("pdfjs-dist/build/pdf.worker.min.mjs?url"),
      file.arrayBuffer(),
    ]);
    signal.throwIfAborted();
    pdf.GlobalWorkerOptions.workerSrc = worker.default;
    const task = pdf.getDocument({ data, stopAtErrors: true, isEvalSupported: false });
    let closing: Promise<void> | undefined;
    const close = () => { closing ??= task.destroy().catch(() => undefined); };
    signal.addEventListener("abort", close, { once: true });
    try {
      const document = await task.promise;
      return document.numPages > maxPages ? `${file.name} 超过 PDF 页数上限 ${maxPages} 页。` : null;
    } finally {
      signal.removeEventListener("abort", close);
      close();
      await closing;
    }
  } catch (error) {
    signal.throwIfAborted();
    if (error instanceof Error && error.name === "PasswordException") {
      return `${file.name} 有打开密码，请解除密码后上传。`;
    }
    if (error instanceof Error && error.name === "InvalidPDFException") {
      return `${file.name} 不是可读取的 PDF 文件。`;
    }
    return null;
  }
}
