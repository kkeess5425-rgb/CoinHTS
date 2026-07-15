import { useState, useEffect, useRef } from 'react'
import useStore from '../store/useStore'

export default function EquityCurve() {
  const canvasRef   = useRef(null)
  const [stats,     setStats]     = useState(null)
  const [entries,   setEntries]   = useState([])
  const [tab,       setTab]       = useState('equity')  // 'equity' | 'distribution' | 'hourly'
  const { activeSymbol } = useStore()

  useEffect(() => {
    const load = async () => {
      try {
        const [sRes, jRes] = await Promise.all([
          fetch(`/api/stats?symbol=${encodeURIComponent(activeSymbol)}`),
          fetch('/api/journal'),
        ])
        setStats(await sRes.json())
        const j = await jRes.json()
        setEntries(j.entries || [])
      } catch {}
    }
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [activeSymbol])

  // Equity 곡선 렌더링
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !entries.length) return
    const ctx = canvas.getContext('2d')
    const W = canvas.width, H = canvas.height

    ctx.clearRect(0, 0, W, H)
    ctx.fillStyle = '#0d1117'
    ctx.fillRect(0, 0, W, H)

    const closed = entries.filter(e => e.pnl_r !== undefined && e.result !== 'OPEN')
    if (!closed.length) return

    // Equity 계산
    const equity = closed.reduce((acc, e) => {
      const last = acc.length ? acc[acc.length-1] : 0
      return [...acc, last + (e.pnl_r || 0)]
    }, [])

    const maxR = Math.max(...equity, 0.1)
    const minR = Math.min(...equity, -0.1)
    const range = maxR - minR || 1

    // 0선
    const zeroY = H - ((0 - minR) / range) * H
    ctx.strokeStyle = '#30363d'
    ctx.lineWidth = 0.5
    ctx.setLineDash([4, 4])
    ctx.beginPath()
    ctx.moveTo(0, zeroY); ctx.lineTo(W, zeroY)
    ctx.stroke()
    ctx.setLineDash([])

    // Equity 곡선
    if (tab === 'equity') {
      const finalR = equity[equity.length - 1]
      const lineColor = finalR >= 0 ? '#3fb950' : '#f85149'
      const fillColor = finalR >= 0 ? '#3fb95015' : '#f8514915'

      ctx.beginPath()
      ctx.moveTo(0, H - ((equity[0] - minR) / range) * H)
      equity.forEach((r, i) => {
        const x = (i / (equity.length - 1)) * W
        const y = H - ((r - minR) / range) * H
        ctx.lineTo(x, y)
      })
      ctx.strokeStyle = lineColor
      ctx.lineWidth = 1.5
      ctx.stroke()

      // 면적
      ctx.lineTo(W, zeroY); ctx.lineTo(0, zeroY)
      ctx.closePath()
      ctx.fillStyle = fillColor
      ctx.fill()

      // 포인트 (WIN/LOSS)
      closed.forEach((e, i) => {
        const x = (i / (equity.length - 1)) * W
        const y = H - ((equity[i] - minR) / range) * H
        ctx.beginPath()
        ctx.arc(x, y, 2.5, 0, Math.PI * 2)
        ctx.fillStyle = e.pnl_r > 0 ? '#3fb950' : '#f85149'
        ctx.fill()
      })
    }

    // R 분포 히스토그램
    if (tab === 'distribution' && stats?.r_distribution?.length) {
      const dist = stats.r_distribution
      const bins = 20
      const dMin = Math.min(...dist); const dMax = Math.max(...dist)
      const bw   = (dMax - dMin) / bins || 1
      const counts = Array(bins).fill(0)
      dist.forEach(r => {
        const idx = Math.min(Math.floor((r - dMin) / bw), bins - 1)
        counts[idx]++
      })
      const maxC = Math.max(...counts)
      const bW   = W / bins

      counts.forEach((c, i) => {
        const x     = i * bW
        const h     = (c / maxC) * (H * 0.8)
        const rMid  = dMin + (i + 0.5) * bw
        ctx.fillStyle = rMid >= 0 ? '#3fb95099' : '#f8514999'
        ctx.fillRect(x + 1, H - h, bW - 2, h)
      })

      // 0 기준선
      const zeroX = ((0 - dMin) / (dMax - dMin)) * W
      ctx.strokeStyle = '#e3b341'
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(zeroX, 0); ctx.lineTo(zeroX, H)
      ctx.stroke()
    }

    // 시간대별 승률 바차트
    if (tab === 'hourly' && stats?.hourly_wr) {
      const wr = stats.hourly_wr
      const hours = Array.from({length: 24}, (_, i) => i)
      const bW = W / 24

      hours.forEach(h => {
        const rate = wr[h] || 0
        const barH = (rate / 100) * (H * 0.8)
        const color = rate >= 60 ? '#3fb950' : rate >= 45 ? '#e3b341' : '#f85149'
        ctx.fillStyle = color + '99'
        ctx.fillRect(h * bW + 1, H - barH, bW - 2, barH)
        if (bW > 16) {
          ctx.fillStyle = '#484f58'
          ctx.font = '8px monospace'
          ctx.textAlign = 'center'
          ctx.fillText(h, h * bW + bW/2, H - 2)
        }
      })

      // 50% 기준선
      ctx.strokeStyle = '#484f58'
      ctx.lineWidth = 0.5
      ctx.setLineDash([3, 3])
      ctx.beginPath()
      ctx.moveTo(0, H * 0.1); ctx.lineTo(W, H * 0.1)
      ctx.stroke()
      ctx.setLineDash([])
    }
  }, [entries, stats, tab])

  const closed = entries.filter(e => e.result !== 'OPEN')
  const wins   = closed.filter(e => e.pnl_r > 0).length
  const cumR   = closed.reduce((s, e) => s + (e.pnl_r || 0), 0)

  return (
    <div className="flex flex-col h-full bg-[#0d1117]">
      <div className="flex items-center gap-1 px-3 py-1.5 bg-[#161b22] border-b border-[#21262d] shrink-0">
        {[['equity','📈 자산'], ['distribution','📊 분포'], ['hourly','⏰ 시간대']].map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)}
            className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
              tab === key ? 'bg-[#1f6feb] text-white' : 'text-[#484f58] hover:text-[#8b949e]'
            }`}>{label}</button>
        ))}
        <div className="ml-auto flex gap-3 text-[10px] text-[#8b949e]">
          {stats && <>
            <span>승률 <span className={stats.win_rate >= 50 ? 'text-[#3fb950]' : 'text-[#f85149]'}>
              {stats.win_rate?.toFixed(1)}%</span></span>
            <span>PF <span className="text-[#c9d1d9]">{stats.profit_factor?.toFixed(2)}</span></span>
            <span>누적R <span style={{ color: cumR >= 0 ? '#3fb950' : '#f85149' }}>
              {cumR > 0 ? '+' : ''}{cumR.toFixed(2)}</span></span>
          </>}
        </div>
      </div>

      {/* 캔버스 */}
      <div className="flex-1 relative min-h-0">
        <canvas
          ref={canvasRef}
          width={600} height={250}
          className="w-full h-full"
          style={{ display: 'block' }}
        />
        {!closed.length && (
          <div className="absolute inset-0 flex items-center justify-center text-[#484f58] text-xs">
            트레이드 데이터 없음
          </div>
        )}
      </div>

      {/* 핵심 지표 */}
      {stats && stats.total_trades > 0 && (
        <div className="grid grid-cols-4 gap-0 border-t border-[#21262d] shrink-0">
          {[
            ['Sharpe',  stats.sharpe_ratio?.toFixed(2)],
            ['Sortino', stats.sortino_ratio?.toFixed(2)],
            ['MDD',     `-${stats.max_drawdown?.toFixed(1)}%`],
            ['평균보유', `${stats.avg_hold_minutes?.toFixed(0)}분`],
          ].map(([label, val], i) => (
            <div key={i} className="py-1.5 text-center border-r last:border-r-0 border-[#21262d]">
              <div className="text-[9px] text-[#484f58]">{label}</div>
              <div className="text-[10px] font-mono text-[#8b949e]">{val}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
