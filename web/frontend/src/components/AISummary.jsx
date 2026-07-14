import { useState, useEffect } from 'react'
import useStore from '../store/useStore'

export default function AISummary() {
  const { activeSymbol, timeframe } = useStore()
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const r = await fetch(`/api/chart-summary?symbol=${encodeURIComponent(activeSymbol)}`)
      const d = await r.json()
      setData(d)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [activeSymbol, timeframe])

  const sectionStyle = "mb-3"
  const labelStyle   = "text-[10px] text-[#58a6ff] font-semibold uppercase tracking-wider mb-1"
  const textStyle    = "text-xs text-[#c9d1d9] leading-relaxed"
  const tagStyle     = "inline-block px-1.5 py-0.5 rounded text-[10px] mr-1 mb-1"

  if (loading) return (
    <div className="h-full flex items-center justify-center text-[#484f58] text-xs animate-pulse">
      AI 분석 중...
    </div>
  )

  if (!data) return (
    <div className="h-full flex items-center justify-center">
      <button onClick={load} className="text-xs text-[#1f6feb] hover:underline">
        AI 분석 실행
      </button>
    </div>
  )

  if (data.error) return (
    <div className="h-full flex flex-col items-center justify-center gap-2">
      <div className="text-[#f85149] text-xs">{data.error}</div>
      <button onClick={load} className="text-xs text-[#1f6feb] hover:underline">다시 시도</button>
    </div>
  )

  return (
    <div className="flex flex-col h-full bg-[#0d1117]">
      {/* 헤더 */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-[#161b22] border-b border-[#21262d] shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-[#8b949e] text-xs">🤖 AI 차트 요약</span>
          <span className="text-[#484f58] text-[10px]">
            {activeSymbol.replace('-USDT-SWAP','')} {timeframe}
          </span>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="text-[10px] text-[#484f58] hover:text-[#8b949e] px-2 py-0.5 rounded border border-[#30363d] transition-colors"
        >
          ↻ 갱신
        </button>
      </div>

      {/* 헤드라인 */}
      <div className="px-3 py-2 bg-[#1f2937]/50 border-b border-[#21262d] shrink-0">
        <div className="text-sm font-semibold text-[#c9d1d9]">{data.headline}</div>
      </div>

      {/* 본문 */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-3">
        {[
          ['📈 추세',     data.trend],
          ['🏗 시장구조', data.structure],
          ['⚡ 오더플로우', data.orderflow],
          ['🎯 주요 레벨', data.key_levels],
          ['⚠️ 리스크',  data.risk],
          ['👀 주목할 것', data.watchfor],
        ].map(([label, text], i) => (
          <div key={i} className={sectionStyle}>
            <div className={labelStyle}>{label}</div>
            <div className={textStyle}>
              {text?.split(', ').map((item, j) => (
                <span
                  key={j}
                  className={`${tagStyle} bg-[#161b22] text-[#8b949e] border border-[#30363d]`}
                >
                  {item}
                </span>
              ))}
            </div>
          </div>
        ))}

        {/* 전체 텍스트 (토글) */}
        <details className="group">
          <summary className="text-[10px] text-[#484f58] cursor-pointer hover:text-[#8b949e] select-none">
            ▶ 전체 보고서 보기
          </summary>
          <div className="mt-2 p-2 bg-[#161b22] rounded border border-[#21262d] text-[10px] text-[#8b949e] leading-relaxed whitespace-pre-wrap">
            {data.full_text}
          </div>
        </details>
      </div>
    </div>
  )
}
