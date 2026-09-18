export type DocumentMeta = {
  translation_model?: string
  translation_provider?: string
  output_ready?: boolean
  translation_run_id?: string | null
  completed_pages?: number[]
  current_page?: number | null
  doc_id: string
  filename: string
  page_count: number
  status: string
  progress: number
  source_lang: string
  target_lang: string
  created_at: string
  message: string
}

export type TranslatedBlock = {
  source_id: string
  page: number
  type: string
  source_text: string
  translated_text: string
  translate: boolean
  protected_hits: string[]
  qa: Record<string, unknown>
}

export type QASummary = {
  numeric_fidelity: boolean
  unit_fidelity: boolean
  formula_fidelity: boolean
  citation_fidelity: boolean
  missing_paragraphs: number
  overall_pass: boolean
  details: string[]
}

export type TranslateResult = {
  translation_run_id?: string | null
  doc_id: string
  status: string
  progress: number
  blocks: TranslatedBlock[]
  qa: QASummary
  layout_report?: { version?: number; status?: string; translated_blocks?: number; protected_regions_checked?: number; retained_blocks?: {page: number; source_id: string; reason: string}[]; details?: string[] }
  output_pdf?: string
  message: string
}

export type LLMConfig = {
  provider: string
  model: string
  api_key: string
  base_url: string
  max_tokens: number
  system_prompt: string
  translation_model: string
  translation_base_url: string
  translation_api_key: string
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || res.statusText)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => request<{ status: string }>('/api/health'),

  upload: async (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<DocumentMeta>('/api/documents/upload', { method: 'POST', body: form })
  },

  listDocs: () => request<DocumentMeta[]>('/api/documents'),

  deleteDoc: (docId: string) =>
    request<{ ok: boolean; doc_id: string }>(`/api/documents/${docId}`, { method: 'DELETE' }),

  getDoc: (docId: string) =>
    request<{ meta: DocumentMeta; result: TranslateResult | null }>(`/api/documents/${docId}`),

  translate: (docId: string, body?: { source_lang?: string; target_lang?: string }) =>
    request<DocumentMeta>(`/api/documents/${docId}/translate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? { source_lang: 'en', target_lang: 'zh' }),
    }),

  rebuildLayout: (docId: string) => request<TranslateResult>(`/api/documents/${docId}/rebuild-layout`, { method: 'POST' }),

  getResult: (docId: string) => request<TranslateResult | { status: string; progress: number; message: string }>(`/api/documents/${docId}/result`),

  retranslate: (docId: string, sourceId: string, body: { manual_text?: string }) =>
    request<TranslatedBlock>(`/api/documents/${docId}/blocks/${sourceId}/retranslate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  chat: (docId: string, question: string, page?: number) =>
    request<{ answer: string; citations: string[] }>('/api/documents/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ doc_id: docId, question, page }),
    }),

  getPublicConfig: () => request<LLMConfig>('/api/llm/config'),

  getLLMConfig: () => request<LLMConfig>('/api/llm/config/raw'),

  saveLLMConfig: (config: LLMConfig) =>
    request<LLMConfig>('/api/llm/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    }),

  testLLM: (config: LLMConfig) =>
    request<{ ok: boolean; message: string }>('/api/llm/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    }),
}
