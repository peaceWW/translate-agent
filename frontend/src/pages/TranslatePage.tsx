import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type DocumentMeta, type TranslateResult } from '../api'
import HomePage from './HomePage'
import { isRunning, statusLabel } from '../documentStatus'

export default function TranslatePage() {
  const { docId } = useParams()
  return docId ? <DocumentReader key={docId} docId={docId} /> : <HomePage />
}

function PDFPage({ docId, page, kind, revision, zoom, runId }: { docId: string; page: number; kind: 'source' | 'target'; revision: number; zoom: number; runId?: string | null }) {
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  return <div className="pdf-sheet">
    {failed ? <div className="empty-state"><p>页面加载失败</p><button className="btn" onClick={() => { setFailed(false); setAttempt(attempt + 1) }}>重新加载</button></div> : <img loading={page === 1 ? 'eager' : 'lazy'} decoding="async" alt={`${kind === 'source' ? '原文' : '译文'} PDF 第 ${page} 页`} src={`/api/documents/${docId}/pages/${page}.png?kind=${kind}&scale=${zoom > 120 ? 2.4 : 1.6}&v=${revision}&run_id=${kind === 'target' ? runId || '' : ''}&retry=${attempt}`} onError={() => setFailed(true)} />}
  </div>
}

function DocumentReader({ docId }: { docId: string }) {
  const [meta, setMeta] = useState<DocumentMeta | null>(null)
  const [result, setResult] = useState<TranslateResult | null>(null)
  const [page, setPage] = useState(1)
  const [view, setView] = useState<'source' | 'target' | 'bilingual'>('bilingual')
  const [zoom, setZoom] = useState(100)
  const [busy, setBusy] = useState(false)
  const [rebuilding, setRebuilding] = useState(false)
  const [revision, setRevision] = useState(0)
  const [error, setError] = useState('')
  const [currentModel, setCurrentModel] = useState('加载中…')
  const viewer = useRef<HTMLDivElement>(null)
  const mutationEpoch = useRef(0)
  useEffect(() => {
    let active = true
    let previousOutput = ''
    let timer: number | undefined
    const tick = async () => {
      const epoch = mutationEpoch.current
      try {
        const data = await api.getDoc(docId)
        if (active && epoch === mutationEpoch.current) {
          setMeta(data.meta)
          setResult(data.result)
          const output = `${data.meta.translation_run_id || 'legacy'}:${data.result?.output_pdf || ''}:${data.result?.layout_report?.version || ''}:${data.meta.status === 'completed' ? data.meta.message : ''}`
          if (output !== previousOutput) { setRevision(r => r + 1); previousOutput = output }
        }
      } catch (e) { if (active) setError(e instanceof Error ? e.message : '加载失败') }
      finally { if (active) timer = window.setTimeout(() => void tick(), 1000) }
    }
    void tick()
    void api.getPublicConfig().then(c => { if (active) setCurrentModel(`${c.provider} / ${c.model}`) }).catch(() => { if (active) setCurrentModel('配置读取失败') })
    return () => { active = false; window.clearTimeout(timer) }
  }, [docId])
  const running = !!meta && isRunning(meta.status)
  const ready = !!result?.output_pdf && !!meta?.output_ready && !running && !rebuilding && !busy
  const start = async () => {
    setBusy(true); setError('')
    try { const next = await api.translate(docId); mutationEpoch.current += 1; setMeta(next); setResult(null) }
    catch (e) { setError(e instanceof Error ? e.message : '启动失败') }
    finally { setBusy(false) }
  }
  const rebuild = async () => {
    setRebuilding(true); setError('')
    try { setResult(await api.rebuildLayout(docId)); const data = await api.getDoc(docId); mutationEpoch.current += 1; setMeta(data.meta); setRevision(r => r + 1) }
    catch (e) { setError(e instanceof Error ? e.message : '重排失败') }
    finally { setRebuilding(false) }
  }
  const goPage = (value: number) => {
    setPage(value)
    const row = viewer.current?.querySelector<HTMLElement>(`[data-page="${value}"]`)
    if (row && viewer.current) viewer.current.scrollTo({ top: row.offsetTop - viewer.current.offsetTop, behavior: 'smooth' })
  }
  const pages = Array.from({ length: meta?.page_count || 0 }, (_, i) => i + 1)
  const layout = result?.layout_report
  return <div className="page workspace pdf-workspace">
    <Link className="back-link" to="/translate">← 返回翻译任务</Link>
    <header className="page-header reader-header"><div><h1>{meta?.filename || '加载文档…'}</h1><p className="muted small">{statusLabel[meta?.status || ''] || '加载中'} · {Math.round(meta?.progress || 0)}% · {meta?.message}</p><div className="model-pill">本任务模型：{meta?.translation_model ? `${meta.translation_provider} / ${meta.translation_model}` : '未记录'}</div></div>
      <div className="header-actions"><a className="btn ghost" href={`/api/documents/${docId}/source.pdf`} target="_blank" rel="noreferrer">原文 PDF ↗</a>{ready && <a className="btn primary" href={`/api/documents/${docId}/translated.pdf?v=${revision}`} download>保存译文 PDF</a>}{result && <button className="btn ghost" disabled={busy || running || rebuilding} onClick={() => void rebuild()}>{rebuilding ? '正在检查并重排…' : '重新排版'}</button>}<button className="btn ghost" disabled={busy || running || rebuilding || !meta} onClick={() => void start()}>{running ? '正在处理…' : result ? '重新翻译' : '开始翻译'}</button><span className="small muted next-model">下次翻译：{currentModel}</span></div>
    </header>
    {error && <p className="error notice" role="alert">{error}</p>}
    {result && meta?.status === 'completed' && !layout?.version && <div className="layout-notice">这是旧版生成的文件，可能存在排版缺失。<button className="btn" disabled={running || rebuilding} onClick={() => void rebuild()}>使用已有译文重新排版</button><span>不调用模型，不重复翻译。</span></div>}
    {layout?.status === 'needs_review' && <details className="layout-notice"><summary>有 {layout.retained_blocks?.length || 0} 处保留原文以避免遮挡或缺字，请核对这些位置</summary>{layout.retained_blocks?.map((b,i) => <p key={i}><button className="text-button" onClick={() => goPage(b.page)}>第 {b.page} 页</button> · {b.reason}<span className="retained-translation">{result?.blocks.find(t => t.source_id === b.source_id)?.translated_text}</span></p>)}</details>}
    {layout?.status === 'failed' && <p className="error notice">PDF 排版检查未通过，未提供译文下载。{layout.details?.join('；')}</p>}
    {(running || (meta?.status === 'failed' && !!meta.completed_pages?.length)) && <div className="page-progress" role="status"><span>已完成 {meta?.completed_pages?.length || 0} / {meta?.page_count || 0} 页</span><progress max={meta?.page_count || 1} value={meta?.completed_pages?.length || 0} /><span>{meta?.status === 'failed' ? '任务中断，已完成的页面仍可阅读' : '每完成一页自动展示，无需等待全文'}</span></div>}
    <div className="reader-toolbar pdf-toolbar"><div className="segmented">{(['source', 'target', 'bilingual'] as const).map(v => <button key={v} className={view === v ? 'active' : ''} onClick={() => setView(v)}>{v === 'source' ? '原文' : v === 'target' ? '译文' : '双语对照'}</button>)}</div><div className="pdf-controls"><button className="btn" disabled={page <= 1} onClick={() => goPage(page-1)}>上一页</button><label>页码 <select value={page} onChange={e => goPage(Number(e.target.value))}>{pages.map(p => <option key={p} value={p}>{p} / {meta?.page_count}</option>)}</select></label><button className="btn" disabled={page >= pages.length} onClick={() => goPage(page+1)}>下一页</button><label>缩放 <select value={zoom} onChange={e => setZoom(Number(e.target.value))}><option value={100}>适合宽度</option><option value={125}>125%</option><option value={150}>150%</option><option value={200}>200%</option></select></label></div></div>
    <div className={`pdf-column-titles ${view === 'bilingual' ? 'dual' : ''}`}>{view !== 'target' && <span>原文 / ORIGINAL</span>}{view !== 'source' && <span>译文 / TRANSLATED</span>}</div>
    <div className="pdf-scroll-view" ref={viewer} onScroll={() => { const container=viewer.current; if (!container) return; const rows=Array.from(container.querySelectorAll<HTMLElement>('[data-page]')); const current=rows.find(row => row.offsetTop + row.offsetHeight > container.scrollTop + container.offsetTop + 60); if(current) setPage(Number(current.dataset.page)) }}>
      <div className="pdf-pages" style={{ width: `${zoom}%` }}>{pages.map(p => <section key={p} className={`pdf-page-pair ${view === 'bilingual' ? 'dual' : ''}`} data-page={p}>
        {view !== 'target' && <div><div className="pdf-page-number">原文 · 第 {p} 页</div><PDFPage docId={docId} page={p} kind="source" revision={revision} zoom={zoom} /></div>}
        {view !== 'source' && <div><div className="pdf-page-number">译文 · 第 {p} 页</div>{!rebuilding && !busy && (ready || meta?.completed_pages?.includes(p)) ? <PDFPage key={`${meta?.translation_run_id}-${revision}`} docId={docId} page={p} kind="target" revision={revision} zoom={zoom} runId={meta?.translation_run_id} /> : <div className="pdf-sheet pdf-wait"><p>{rebuilding ? '正在使用已有译文重排…' : meta?.status === 'failed' ? '该页尚未完成，请重试翻译' : meta?.current_page === p ? `第 ${p} 页正在翻译和排版…` : running ? `第 ${p} 页等待翻译` : '译文 PDF 尚未生成'}</p><span>原文图形与公式仍可完整查看</span></div>}</div>}
      </section>)}</div>
    </div>
    <div className="pdf-viewer-foot"><span>左右同页同步阅读 · 显示实际 PDF 页面</span><span>{layout?.status === 'passed' ? `排版检查通过 · 已核对 ${layout.protected_regions_checked || 0} 个保护区域` : '图表和公式保留原稿，异常位置单独提示'}</span></div>
    {result?.qa && <details className={`quality-details ${result.qa.overall_pass ? 'pass' : 'warn'}`}><summary>{running || meta?.status === 'failed' ? '已完成页面保真检查：' : '内容保真检查：'}{result.qa.overall_pass ? '通过' : '有待核对项'}</summary><p className="small muted">自动检查不能替代学术语义核对。</p>{result.qa.details.map((d,i) => <p key={i} className="small">{d}</p>)}</details>}
  </div>
}
