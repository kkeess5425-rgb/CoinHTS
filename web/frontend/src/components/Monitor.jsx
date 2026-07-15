import { useState, useEffect } from 'react'

// ── 시스템 모니터 ─────────────────────────────────────
export function SystemMonitor() {
  const [data, setData] = useState(null)

  useEffect(() => {
    const load = () => fetch('/api/monitor').then(r => r.json()).then(setData).catch(() => {})
    load()
    const t = setInterval(load, 2000)
    return () => clearInterval(t)
  }, [])

  const latest = data?.latest || {}
  const stats  = data?.stats  || {}
  const history= data?.history || {}

  const GaugeBar = ({ value, max = 100, color }) => (
    <div className="flex-1 h-1.5 bg-[#21262d] rounded-full overflow-hidden">
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{ width: `${Math.min(100, (value / max) * 100)}%`, background: color }}
      />
    </div>
  )

  const Metric = ({ label, value, unit = '', color = '#8b949e', max }) => (
    <div className="space-y-0.5">
      <div className="flex justify-between text-[10px]">
        <span className="text-[#484f58]">{label}</span>
        <span style={{ color }}>{value}{unit}</span>
      </div>
      {max && <GaugeBar value={Number(value)} max={max} color={color} />}
    </div>
  )

  // Mini 히스토리 차트 (SVG)
  const MiniChart = ({ data, color, label }) => {
    if (!data?.length) return null
    const max = Math.max(...data, 0.01)
    const w = 80, h = 20
    const pts = data.map((v, i) => {
      const x = (i / (data.length - 1)) * w
      const y = h - (v / max) * h
      return `${x},${y}`
    }).join(' ')
    return (
      <div className="flex items-center gap-1">
        <span className="text-[9px] text-[#484f58] w-8">{label}</span>
        <svg viewBox={`0 0 ${w} ${h}`} width={w} height={h}>
          <polyline points={pts} fill="none" stroke={color} strokeWidth="1" />
        </svg>
      </div>
    )
  }

  const uptime = stats.uptime_sec || 0
  const uptimeStr = `${Math.floor(uptime/3600)}h ${Math.floor((uptime%3600)/60)}m`
  const cpuColor  = latest.cpu_pct > 80 ? '#f85149' : latest.cpu_pct > 60 ? '#e3b341' : '#3fb950'
  const memColor  = latest.mem_pct > 85 ? '#f85149' : latest.mem_pct > 70 ? '#e3b341' : '#3fb950'

  return (
    <div className="flex flex-col h-full bg-[#0d1117]">
      <div className="px-3 py-1.5 bg-[#161b22] border-b border-[#21262d] text-[#8b949e] text-xs">
        🖥️ 시스템 모니터
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {/* 가동 시간 */}
        <div className="text-center py-1">
          <div className="text-[#484f58] text-[10px]">가동 시간</div>
          <div className="text-[#c9d1d9] text-sm font-mono font-semibold">{uptimeStr}</div>
        </div>

        {/* 실시간 지표 */}
        <div className="space-y-2">
          <Metric label="CPU" value={latest.cpu_pct?.toFixed(0) || 0} unit="%" color={cpuColor} max={100} />
          <Metric label="MEM" value={latest.mem_pct?.toFixed(0) || 0} unit="%" color={memColor} max={100} />
          <Metric label="TPS" value={latest.ticks_per_sec?.toFixed(0) || 0} unit="/s" color="#58a6ff" max={200000} />
          <Metric label="지연" value={latest.ws_latency_ms?.toFixed(0) || 0} unit="ms" color="#bc8cff" />
          <Metric label="태스크" value={latest.active_tasks || 0} color="#8b949e" />
        </div>

        {/* 누적 통계 */}
        <div className="bg-[#161b22] rounded p-2 space-y-1">
          <div className="text-[10px] text-[#484f58] mb-1">누적 통계</div>
          {[
            ['총 틱', (stats.total_ticks || 0).toLocaleString()],
            ['평균 TPS', (stats.avg_tps || 0).toFixed(0)],
            ['최고 TPS', (stats.peak_tps || 0).toFixed(0)],
            ['평균 지연', `${(stats.avg_latency || 0).toFixed(0)}ms`],
            ['오류', stats.error_count || 0],
          ].map(([k, v], i) => (
            <div key={i} className="flex justify-between text-[10px]">
              <span className="text-[#484f58]">{k}</span>
              <span className="font-mono text-[#8b949e]">{v}</span>
            </div>
          ))}
        </div>

        {/* 히스토리 미니 차트 */}
        <div className="space-y-1">
          <div className="text-[10px] text-[#484f58]">1분 히스토리</div>
          <MiniChart data={history.cpu}    color="#3fb950" label="CPU" />
          <MiniChart data={history.mem}    color="#e3b341" label="MEM" />
          <MiniChart data={history.tps}    color="#58a6ff" label="TPS" />
          <MiniChart data={history.latency}color="#bc8cff" label="LAT" />
        </div>
      </div>
    </div>
  )
}

// ── 포지션 사이저 ─────────────────────────────────────
export function PositionSizer() {
  const [params, setParams] = useState({ entry: 65000, sl: 64500, atr: 500, method: 'atr', account: 10000, risk_pct: 1 })
  const [result, setResult] = useState(null)

  const calc = async () => {
    const q = new URLSearchParams({
      entry:    params.entry,
      sl:       params.sl,
      atr:      params.atr,
      method:   params.method,
      account:  params.account,
      risk_pct: params.risk_pct,
    })
    const res = await fetch(`/api/position-size?${q}`)
    setResult(await res.json())
  }

  const upd = (k, v) => setParams(p => ({...p, [k]: Number(v)}))

  return (
    <div className="flex flex-col h-full bg-[#0d1117]">
      <div className="px-3 py-1.5 bg-[#161b22] border-b border-[#21262d] text-[#8b949e] text-xs">
        📐 포지션 사이저
      </div>
      <div className="p-3 space-y-2">
        {/* 입력 */}
        {[
          ['진입가', 'entry'], ['SL', 'sl'], ['ATR', 'atr'],
          ['계좌(USD)', 'account'], ['리스크(%)', 'risk_pct'],
        ].map(([label, key]) => (
          <div key={key} className="flex items-center gap-2">
            <label className="text-[10px] text-[#484f58] w-16 shrink-0">{label}</label>
            <input type="number" value={params[key]}
              onChange={e => upd(key, e.target.value)}
              className="flex-1 bg-[#21262d] text-[#c9d1d9] border border-[#30363d] rounded px-2 py-0.5 text-xs"
            />
          </div>
        ))}
        <div className="flex items-center gap-2">
          <label className="text-[10px] text-[#484f58] w-16 shrink-0">방법</label>
          <select value={params.method} onChange={e => setParams(p => ({...p, method: e.target.value}))}
            className="flex-1 bg-[#21262d] text-[#c9d1d9] border border-[#30363d] rounded px-2 py-0.5 text-xs">
            <option value="atr">ATR 기반</option>
            <option value="kelly">Kelly</option>
            <option value="fixed">고정 비율</option>
          </select>
        </div>
        <button onClick={calc}
          className="w-full py-1.5 bg-[#1f6feb] hover:bg-[#388bfd] text-white rounded text-xs transition-colors">
          계산
        </button>

        {result && (
          <div className="space-y-1.5 mt-2">
            {Object.entries(result.methods || {}).map(([method, r]) => (
              <div key={method} className={`p-2 rounded border text-xs ${
                method === result.recommended ? 'border-[#1f6feb] bg-[#1f6feb]/10' : 'border-[#21262d] bg-[#161b22]'
              }`}>
                <div className="flex justify-between mb-1">
                  <span className="font-semibold text-[#c9d1d9] uppercase">{method}</span>
                  {method === result.recommended && (
                    <span className="text-[10px] text-[#1f6feb]">★ 추천</span>
                  )}
                </div>
                {[
                  ['크기', r.size?.toFixed(4)],
                  ['리스크', `$${r.risk_amount?.toFixed(0)}`],
                  ['명목', `$${r.notional?.toFixed(0)}`],
                  ['레버리지', `${r.leverage?.toFixed(1)}×`],
                ].map(([k, v]) => (
                  <div key={k} className="flex justify-between text-[10px]">
                    <span className="text-[#484f58]">{k}</span>
                    <span className="font-mono text-[#8b949e]">{v}</span>
                  </div>
                ))}
                <div className="text-[9px] text-[#484f58] mt-1">{r.rationale}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
