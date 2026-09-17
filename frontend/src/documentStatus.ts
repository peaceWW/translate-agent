export const statusLabel: Record<string, string> = { uploaded: '待翻译', queued: '排队中', parsing: '解析中', translating: '翻译中', qa: '自动校验中', composing: '排版中', completed: '已完成', failed: '翻译失败' }
export const isRunning = (status: string) => ['queued', 'parsing', 'translating', 'qa', 'composing'].includes(status)
