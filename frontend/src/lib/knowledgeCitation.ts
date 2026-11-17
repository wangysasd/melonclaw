export interface KnowledgeTextReference {
  kbId: string;
  documentId: string;
  revisionId: string;
  offset: number;
  limit: number;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const PATH = /^\/api\/knowledge-bases\/([^/]+)\/documents\/([^/]+)\/text$/;

/** Classify only same-origin knowledge text links; never turns arbitrary links into API requests. */
export function parseKnowledgeTextReference(href: string, origin: string):
  | { kind: "not-knowledge" }
  | { kind: "invalid" }
  | { kind: "reference"; value: KnowledgeTextReference } {
  let url: URL;
  try {
    url = new URL(href, origin);
  } catch {
    return { kind: "not-knowledge" };
  }
  if (url.origin !== origin || !url.pathname.startsWith("/api/knowledge-bases/")) {
    return { kind: "not-knowledge" };
  }
  const path = PATH.exec(url.pathname);
  if (!path) return { kind: "not-knowledge" };
  const kbId = path[1];
  const documentId = path[2];
  const revisions = url.searchParams.getAll("revision_id");
  const offsets = url.searchParams.getAll("offset");
  const limits = url.searchParams.getAll("limit");
  if (revisions.length !== 1 || offsets.length > 1 || limits.length > 1) return { kind: "invalid" };
  const revisionId = revisions[0];
  const offset = offsets.length ? Number(offsets[0]) : 0;
  const limit = limits.length ? Number(limits[0]) : 4000;
  if (!UUID.test(kbId) || !UUID.test(documentId) || !UUID.test(revisionId)
    || !Number.isSafeInteger(offset) || offset < 0
    || !Number.isSafeInteger(limit) || limit < 1 || limit > 8000) {
    return { kind: "invalid" };
  }
  return { kind: "reference", value: { kbId, documentId, revisionId, offset, limit } };
}


export function knowledgeReferenceKey(href: string, origin: string): string | null {
  const text = parseKnowledgeTextReference(href, origin);
  if (text.kind !== "reference") return null;
  const value = text.value;
  return `doc:${value.kbId}:${value.documentId}:${value.revisionId}:${value.offset}:${value.limit}`;
}

