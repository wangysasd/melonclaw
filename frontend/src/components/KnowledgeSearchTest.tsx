import { useEffect, useRef, useState } from "react";
import { Alert, Button, Empty, Form, Input, InputNumber, Select, Space, Tag } from "antd";
import { knowledgeApi, type KnowledgeBase, type KnowledgeDocument, type KnowledgeSearchInput, type KnowledgeSearchResult } from "../api/knowledge";
import { KnowledgeCitationProvider, useKnowledgeCitation } from "./KnowledgeCitationProvider";

const branchNames = { vector: "向量", keyword: "关键词", fused: "RRF 融合", sources: "最终返回" } as const;
const branchStatus = { ok: "成功", failed: "失败", skipped: "跳过" } as const;

export function KnowledgeSearchTest({ base }: { base: KnowledgeBase }) {
  return <KnowledgeCitationProvider><SearchForm key={base.id} base={base} /></KnowledgeCitationProvider>;
}

function SearchForm({ base }: { base: KnowledgeBase }) {
  const [form] = Form.useForm<KnowledgeSearchInput>();
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [nextPage, setNextPage] = useState<number | null>(1);
  const [documentsLoading, setDocumentsLoading] = useState(false);
  const [documentsError, setDocumentsError] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<KnowledgeSearchResult | null>(null);
  const searchController = useRef<AbortController | null>(null);
  const documentsController = useRef<AbortController | null>(null);
  const citation = useKnowledgeCitation();

  useEffect(() => () => {
    searchController.current?.abort();
    documentsController.current?.abort();
  }, []);

  const loadDocuments = async () => {
    if (nextPage === null || documentsController.current) return;
    const controller = new AbortController();
    documentsController.current = controller;
    setDocumentsLoading(true); setDocumentsError("");
    try {
      const response = await knowledgeApi.documents(base.id, nextPage, controller.signal);
      if (controller.signal.aborted) return;
      setDocuments((current) => [...new Map([...current, ...response.items].map((doc) => [doc.id, doc])).values()]);
      setNextPage(nextPage * 20 < response.total ? nextPage + 1 : null);
    } catch (reason) {
      if (!controller.signal.aborted) setDocumentsError(reason instanceof Error ? reason.message : "文档范围加载失败");
    } finally {
      if (!controller.signal.aborted) setDocumentsLoading(false);
      if (documentsController.current === controller) documentsController.current = null;
    }
  };

  const search = async (values: KnowledgeSearchInput) => {
    searchController.current?.abort();
    const controller = new AbortController();
    searchController.current = controller;
    setLoading(true); setError(""); setResult(null);
    try {
      const response = await knowledgeApi.search(base.id, { ...values, query: values.query.trim() }, controller.signal);
      if (!controller.signal.aborted) setResult(response);
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "检索失败，请稍后重试");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  };

  return <section className="knowledge-search-test" aria-label="检索测试">
    <p className="resource-hint">仅检索本库已发布资料，不生成回答。排序值用于比较相关性，不代表答案可信度。</p>
    <p className="resource-hint">混合检索并行召回向量与关键词结果，按 RRF 排名融合；向量相关度阈值只作用于向量分支。保留单路模式用于对照。</p>
    <Form layout="vertical" form={form} initialValues={{ query: "", mode: "hybrid", top_k: 6, document_ids: [] }} onFinish={(values: KnowledgeSearchInput) => void search(values)}>
      <Form.Item name="query" label="检索问题" rules={[{ required: true, whitespace: true, message: "请输入检索问题" }, { max: 2000 }]}>
        <Input.TextArea rows={3} maxLength={2000} placeholder="输入想查找的内容" />
      </Form.Item>
      <div className="knowledge-search-controls">
        <Form.Item name="mode" label="原文检索方式"><Select options={[{ value: "hybrid", label: "混合检索" }, { value: "vector", label: "向量检索" }, { value: "keyword", label: "关键词检索" }]} /></Form.Item>
        <Form.Item name="top_k" label="保留条数" rules={[{ required: true, type: "integer", min: 1, max: 20 }]}><InputNumber min={1} max={20} precision={0} /></Form.Item>
      </div>
      <Form.Item name="document_ids" label="文档范围" extra="留空检索全库；最多20份。">
        <Select mode="multiple" maxCount={20} allowClear showSearch optionFilterProp="label" placeholder="全部已发布文档" loading={documentsLoading}
          onOpenChange={(open) => { if (open && nextPage === 1) void loadDocuments(); }}
          options={documents.filter((doc) => doc.active_revision_id).map((doc) => ({ value: doc.id, label: doc.display_name }))} />
      </Form.Item>
      {documentsError ? <Alert type="error" showIcon title={documentsError} /> : null}
      <Space wrap>
        <Button type="primary" htmlType="submit" loading={loading}>开始检索</Button>
        {nextPage !== null ? <Button className="action-secondary" loading={documentsLoading} onClick={() => void loadDocuments()}>{documents.length ? "加载更多文档" : "加载文档范围"}</Button> : null}
      </Space>
    </Form>
    <div aria-live="polite" aria-busy={loading}>
      {error ? <Alert type="error" showIcon title={error} /> : null}
      {result ? <>
        {result.notice ? <Alert type="warning" showIcon title={result.notice} /> : null}
        <p>实际返回 {result.items.length} 条片段{result.duration_ms !== undefined ? `，总耗时 ${(result.duration_ms / 1000).toFixed(1)} 秒` : ""}。</p>
        {Object.entries(result.branches).map(([route, info]) => info ? <p key={route}>
          {branchNames[route as keyof typeof branchNames]}：候选 {info.candidates} / 返回 {info.returned}
          {info.below_threshold ? ` / 低于向量相关度 ${info.below_threshold}` : ""}
          {info.duration_ms !== undefined ? ` / ${info.duration_ms} ms` : ""} · {branchStatus[info.status]}{info.error_code ? `（${info.error_code}）` : ""}
        </p> : null)}
        {result.items.length === 0 ? <Empty description={Object.values(result.branches).some((info) => info?.below_threshold) ? "没有达到相关度的资料" : "未找到相关片段，可调整问题或文档范围后重试"} /> : result.items.map((item, index) => <article className="knowledge-search-result" key={item.chunk_id}>
          <h3>{index + 1}. {item.title}</h3>
          <Tag>原文依据</Tag>
          <div className="knowledge-search-meta">
            {item.source_locator?.page !== undefined ? <Tag>第 {item.source_locator.page} 页</Tag> : null}
            {item.source_locator?.slide !== undefined ? <Tag>幻灯片 {item.source_locator.slide}</Tag> : null}
            {item.source_locator?.sheet !== undefined ? <Tag>工作表：{item.source_locator.sheet}</Tag> : null}
            {item.source_locator?.block !== undefined ? <Tag>正文块 {item.source_locator.block}</Tag> : null}
            {item.source_locator?.row !== undefined ? <Tag>第 {item.source_locator.row} 行</Tag> : null}
            {item.source_locator?.method ? <Tag>{item.source_locator.method === "ocr" ? "OCR" : item.source_locator.method === "office" ? "Office 文本提取" : "文本提取"}</Tag> : null}
            <span>{result.mode === "vector" ? "余弦相似度" : result.mode === "keyword" ? "关键词排序值" : "RRF 排序值"}：{item.score.toFixed(4)}</span>
            {item.vector_rank !== undefined ? <span>向量：第 {item.vector_rank} 名{item.vector_score !== undefined ? ` / 余弦 ${item.vector_score.toFixed(4)}` : ""}</span> : null}
            {item.keyword_rank !== undefined ? <span>关键词：第 {item.keyword_rank} 名{item.keyword_score !== undefined ? ` / 排序值 ${item.keyword_score.toFixed(4)}` : ""}</span> : null}
            {item.matched_terms !== undefined ? <span>命中词数：{item.matched_terms}</span> : null}
          </div>
          <p className="knowledge-search-version">版本：{item.revision_id} · 字符位置：{item.start_offset}–{item.snippet_end_offset}</p>
          <pre className="knowledge-search-snippet">{item.content}</pre>
          <Button className="action-secondary" onClick={() => citation?.openFromHref(item.source_url)}>查看原文</Button>
        </article>)}
      </> : null}
    </div>
  </section>;
}
