import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type DocumentMeta } from '../api'

export default function HistoryPage() {
  const [docs, setDocs] = useState<DocumentMeta[]>([])
  const navigate = useNavigate()

  useEffect(() => {
    void api.listDocs().then(setDocs).catch(() => undefined)
  }, [])

  return (
    <div className="page">
      <header className="page-header">
        <h1>历史记录</h1>
      </header>
      <div className="table-card">
        <table>
          <thead>
            <tr>
              <th>文件</th>
              <th>页数</th>
              <th>状态</th>
              <th>时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.doc_id}>
                <td>{d.filename}</td>
                <td>{d.page_count}</td>
                <td>{d.status}</td>
                <td>{new Date(d.created_at).toLocaleString()}</td>
                <td>
                  <button className="btn ghost" onClick={() => navigate(`/translate/${d.doc_id}`)}>
                    打开
                  </button>
                </td>
              </tr>
            ))}
            {!docs.length && (
              <tr>
                <td colSpan={5} className="muted">
                  暂无历史
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
