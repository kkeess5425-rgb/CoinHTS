import { useState, useEffect } from 'react'
import useStore from '../store/useStore'

const TF_OPTIONS = ['1m', '5m', '15m', '1H', '4H']
const GRADE_STYLE = {
  'A+': { bg: '#3fb95020', border: '#3fb950', text: '#3fb950' },
  'A':  { bg: '#58a6ff20', border: '#58a6ff', text: '#58a6ff' },
  'B':  { bg: '#e3b34120', border: '#e3b341', text: '#e3b341' },
  'C':  { bg: '#f8514920', border: '#f85149', text: '#f85149' },
  'D':  { bg: '#21262d',   border: '#30363d', text: '#484f58' },
}

export default function MTFAnalysis() {
  const { activeSymbol }  = useStore()
  const [loading, setLoading] = useState(false)
  const [data,    setData]    = useState(null)
  const [error,   setError]   = useState(null)
  const [tfs, setTfs] = useState({ htf: '4H', mtf: '1H', ltf: '15m' })

  const run = async () => {
    setLoading(true); setError(null)
    try {
      const q = new URLSearchParams({ symbol: activeSymbol, ...tfs, mtf_tf: tfs.mtf })
      const r = await fetch(`/api/mtf-analysis?${q}`)
      const d = await r.json()
      if (d.error) { setError(d.error); return }
      setData(d)
    } catch (e) { setError(String(e)) }
    finally { setLoading(false) }
  }

  useEffect(() => { run() }, [activeSymbol])

  const conf = data?.confluence || {}
  const grade = conf.grade || 'D'
  const gs = GRADE_STYLE[grade] || GRADE_STYLE['D']
  const isLong = data?.direction === 'LONG'

  return (
    <div className="flex flex-col h-full bg-[#0d1117]">
      {/* 헤더 */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-[#161b22] border-b border-[#21262d] shrink-0">
        <span className="text-[#8b949e] text-xs">🔭 MTF 분석</span>
        <div className="flex gap-1 ml-auto">
          {[['htf','HTF'],['mtf','MTF'],['ltf','LTF']].map(([key, label]) => (
            <select key={key}
              value={tfs[key]}
              onChange={e => setTfs(t => ({...t, [key]: e.target.value}))}
              className="bg-[#21262d] text-[#8b949e] border border-[#30363d] rounded px-1 py-0.5 text-[10px]"
            >
              {TF_OPTIONS.map(tf => <option key={tf}>{tf}</option>)}
            </select>
          ))}
          <button onClick={run} disabled={loading}
            className="px-2 py-0.5 bg-[#1f6feb] text-white rounded text-[10px] transition-colors disabled:opacity-50">
            {loading ? '...' : '▶'}
          </button>
        </div>
      </div>

      {error && (
        <div className="mx-3 my-2 px-2 py-1 bg-[#f85149]/10 border border-[#f85149]/30 rounded text-[#f85149] text-[10px]">
          ⚠️ {error}
        </div>
      )}

      {data && (
        <div className="flex-1 overflow-y-auto p-3 space-y-3">
          {/* 방향 + 정렬 상태 */}
          <div className="flex items-center gap-2">
            <div className={`flex-1 py-2 rounded text-center text-sm font-bold border ${
              data.aligned
                ? (isLong ? 'bg-[#3fb950]/10 border-[#3fb950] text-[#3fb950]'
                          : 'bg-[#f85149]/10 border-[#f85149] text-[#f85149]')
                : 'bg-[#21262d] border-[#30363d] text-[#484f58]'
            }`}>
              {data.direction ? (isLong ? '🔼 LONG' : '🔽 SHORT') : '— 대기'}
            </div>
            <div className="text-center">
              <div className="text-[10px] text-[#484f58]">정렬</div>
              <div className={`text-lg ${data.aligned ? 'text-[#3fb950]' : 'text-[#f85149]'}`}>
                {data.aligned ? '✅' : '❌'}
              </div>
            </div>
          </div>

          {/* MTF 점수 */}
          <div className="flex items-center gap-2">
            <div className="flex-1 h-2 bg-[#21262d] rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-700"
                style={{
                  width: `${data.mtf_score || 0}%`,
                  background: data.mtf_score >= 70 ? '#3fb950' : data.mtf_score >= 50 ? '#e3b341' : '#f85149',
                }}
              />
            </div>
            <span className="text-xs font-mono text-[#c9d1d9] w-12 text-right">
              {data.mtf_score?.toFixed(0)}/100
            </span>
          </div>

          {/* 컨플루언스 등급 */}
          {conf.total > 0 && (
            <div className={`rounded p-2 border text-center`}
                 style={{ background: gs.bg, borderColor: gs.border }}>
              <div className="text-xs" style={{ color: gs.text }}>
                컨플루언스 등급
              </div>
              <div className="text-2xl font-bold" style={{ color: gs.text }}>
                {grade}
              </div>
              <div className="text-xs text-[#8b949e]">
                {conf.total?.toFixed(0)}/100 {conf.is_tradeable ? '— 거래 가능 ✅' : '— 대기 권고'}
              </div>
            </div>
          )}

          {/* 진입 레벨 */}
          {conf.entry && (
            <div className="bg-[#161b22] rounded p-2 space-y-1">
              <div className="text-[10px] text-[#484f58]">진입 레벨</div>
              {[
                ['진입가', conf.entry?.toFixed(1), '#58a6ff'],
                ['SL',    conf.sl?.toFixed(1),    '#f85149'],
                ['TP1',   conf.tp?.toFixed(1),    '#3fb950'],
                ['TP2',   conf.tp2?.toFixed(1),   '#3fb95066'],
              ].filter(([,v]) => v).map(([label, val, color]) => (
                <div key={label} className="flex justify-between text-xs">
                  <span className="text-[#8b949e]">{label}</span>
                  <span className="font-mono" style={{ color }}>{val}</span>
                </div>
              ))}
            </div>
          )}

          {/* 컨플루언스 근거 */}
          {data.confluence_reasons?.length > 0 && (
            <div className="space-y-1">
              <div className="text-[10px] text-[#484f58]">컨플루언스 근거</div>
              {data.confluence_reasons.map((r, i) => (
                <div key={i} className="text-[10px] text-[#8b949e] bg-[#161b22] rounded px-2 py-1">
                  {r}
                </div>
              ))}
            </div>
          )}

          {/* 핵심 레벨 */}
          {data.key_levels && (
            <div className="bg-[#161b22] rounded p-2">
              <div className="text-[10px] text-[#484f58] mb-1">핵심 레벨</div>
              {data.key_levels.eqh?.length > 0 && (
                <div className="flex justify-between text-[10px]">
                  <span className="text-[#e3b341]">EQH</span>
                  <span className="font-mono text-[#8b949e]">{data.key_levels.eqh.map(p => p?.toFixed(0)).join(', ')}</span>
                </div>
              )}
              {data.key_levels.eql?.length > 0 && (
                <div className="flex justify-between text-[10px]">
                  <span className="text-[#58a6ff]">EQL</span>
                  <span className="font-mono text-[#8b949e]">{data.key_levels.eql.map(p => p?.toFixed(0)).join(', ')}</span>
                </div>
              )}
              {data.key_levels.mtf_fvg?.length > 0 && data.key_levels.mtf_fvg.map((fvg, i) => (
                <div key={i} className="flex justify-between text-[10px]">
                  <span className={fvg.dir === 'bull' ? 'text-[#3fb950]' : 'text-[#f85149]'}>
                    FVG ({fvg.dir})
                  </span>
                  <span className="font-mono text-[#8b949e]">{fvg.bot?.toFixed(0)}~{fvg.top?.toFixed(0)}</span>
                </div>
              ))}
            </div>
          )}

          {/* 요약 */}
          {data.summary && (
            <div className="text-[10px] text-[#484f58] bg-[#161b22] rounded px-2 py-1.5">
              {data.summary}
            </div>
          )}
        </div>
      )}

      {!data && !loading && !error && (
        <div className="flex items-center justify-center h-full text-[#484f58] text-xs">
          ▶ 분석 실행
        </div>
      )}
    </div>
  )
}
