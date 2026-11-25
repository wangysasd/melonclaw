import { useEffect, useRef, useState } from "react";
import { fetchAuthorizedBlob } from "../api/client";
import { ImageLightbox } from "./ImageLightbox";

export function useAuthorizedObjectUrl(url: string, onError?: () => void): string {
  const [objectUrl, setObjectUrl] = useState("");
  const report = useRef(onError);
  report.current = onError;
  useEffect(() => {
    const controller = new AbortController();
    let current = "";
    void fetchAuthorizedBlob(url, controller.signal).then((blob) => {
      current = URL.createObjectURL(blob);
      setObjectUrl(current);
    }).catch(() => {
      if (!controller.signal.aborted) {
        setObjectUrl("");
        report.current?.();
      }
    });
    return () => {
      controller.abort();
      if (current) URL.revokeObjectURL(current);
    };
  }, [url]);
  return objectUrl;
}

export function AuthorizedImage({ url, alt, className, onError }: { url: string; alt: string; className?: string; onError?: () => void }) {
  const src = useAuthorizedObjectUrl(url, onError);
  if (!src) return null;
  return <img src={src} alt={alt} className={className} onError={onError} />;
}

export function AuthorizedLightbox({ url, alt, onClose }: { url: string; alt: string; onClose: () => void }) {
  const src = useAuthorizedObjectUrl(url);
  if (!src) return null;
  return <ImageLightbox src={src} alt={alt} onClose={onClose} />;
}

export async function downloadAuthorized(url: string, fileName: string) {
  const blob = await fetchAuthorizedBlob(url);
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}
