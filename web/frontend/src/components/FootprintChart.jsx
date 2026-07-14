import { useEffect, useRef, useState, useCallback } from 'react'
import useStore from '../store/useStore'

const COLORS = {
  bg:       '#0d1117',
  grid:     '#21262d',
  buyWeak:  '#0d2b1a',
  buyStrong:'#2ea043',
  sellWeak: '#2d1117',
  sellStrong:'#c93c37',
  imb:      '#1a3a2a',
  imbBear:  '#3a1a1a',
  poc:      '#ff9800',
  delta:    { pos: '#3fb950', neg: '#f85149' },
  text:     '#8b949e',
}

export default function FootprintChart() {
  const canvasRef   = useRef(null)
  const { activeSymbol } = useStore()
  const [bars,  setBars]  = useState([])
  const [tickSz, setTickSz] = useState(10)  // 표시 틱 크기 (px)
  const loading = useRef(false)

  // 데이터 로드
  const load = useCallback(async () => {
    if (loading.current) return
    loading.current = true
    try {
      const res  = await fetch(`/api/footprint/${encodeURIComponent(activeSymbol)}?limit=15`)
      const data = await res.json()
      setBars(data.bars || [])
    } catch (e) {
      console.warn('Footprint 로드 오류:', e)
    } finally {
      loading.current = false
    }
  }, [activeSymbol])

  useEffect(() => {
    load()
    const t = setInterval(load, 2000)
    return () => clearInterval(t)
  }, [load])

  // 캔버스 렌더링
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !bars.length) return
    const ctx = canvas.getContext('2d')
    const W = canvas.width, H = canvas.height

    ctx.fillStyle = COLORS.bg
    ctx.fillRect(0, 0, W, H)

    if (!bars.length) return

    // 가격 범위
    const allPrices = bars.flatMap(b => b.cells.map(c => c.p))
    const pMin = Math.min(...allPrices)
    const pMax = Math.max(...allPrices)
    const pRange = pMax - pMin || 1

    const BAR_W   = (W - 60) / bars.length
    const PRICE_H = H - 40  // 하단 delta bar 공간

    // 그리드
    ctx.strokeStyle = COLORS.grid
    ctx.lineWidth   = 0.5
    ctx.beginPath()
    for (let i = 0; i <= 5; i++) {
      const y = (i / 5) * PRICE_H
      ctx.moveTo(60, y); ctx.lineTo(W, y)
    }
    ctx.stroke()

    bars.forEach((bar, bi) => {
      const x0  = 60 + bi * BAR_W
      const maxVol = Math.max(...bar.cells.map(c => c.bv + c.sv), 0.001)

      bar.cells.forEach(cell => {
        const y   = PRICE_H - ((cell.p - pMin) / pRange) * PRICE_H
        const h   = Math.max((1 / (pRange || 1)) * PRICE_H, 4)
        const tot = cell.bv + cell.sv
        const ratio = tot / maxVol

        // 배경색
        const isBuy = cell.bv >= cell.sv
        const isImb = cell.sv > 0 && cell.bv / cell.sv >= 4
        const isImbBear = cell.bv > 0 && cell.sv / cell.bv >= 4

        if (isImb)     ctx.fillStyle = COLORS.imb
        else if (isImbBear) ctx.fillStyle = COLORS.imbBear
        else if (isBuy) {
          const alpha = 0.2 + ratio * 0.6
          ctx.fillStyle = `rgba(46, 160, 67, ${alpha})`
        } else {
          const alpha = 0.2 + ratio * 0.6
          ctx.fillStyle = `rgba(201, 60, 55, ${alpha})`
        }
        ctx.fillRect(x0, y - h/2, BAR_W - 1, h)

        // POC 마커
        if (bar.poc && Math.abs(cell.p - bar.poc) < 0.01) {
          ctx.strokeStyle = COLORS.poc
          ctx.lineWidth   = 1.5
          ctx.strokeRect(x0, y - h/2, BAR_W - 1, h)
        }

        // 볼륨 텍스트 (셀이 충분히 클 때만)
        if (h >= 10 && BAR_W >= 80) {
          ctx.font      = '9px monospace'
          ctx.textAlign = 'right'
          ctx.fillStyle = '#f85149'
          ctx.fillText(cell.sv.toFixed(1), x0 + BAR_W/2 - 2, y + 3)
          ctx.textAlign = 'left'
          ctx.fillStyle = '#3fb950'
          ctx.fillText(cell.bv.toFixed(1), x0 + BAR_W/2 + 2, y + 3)
        }
      })

      // 캔들 윤곽
      ctx.strokeStyle = bar.delta >= 0 ? '#26a641' : '#f85149'
      ctx.lineWidth   = 1
      const candleTop = PRICE_H - ((bar.high - pMin) / pRange) * PRICE_H
      const candleBot = PRICE_H - ((bar.low  - pMin) / pRange) * PRICE_H
      ctx.beginPath()
      ctx.moveTo(x0 + BAR_W/2, candleTop)
      ctx.lineTo(x0 + BAR_W/2, candleBot)
      ctx.stroke()

      // Delta 바 (하단)
      const dH  = Math.min(Math.abs(bar.delta) / (maxVol + 0.001) * 30, 35)
      const dY  = H - (bar.delta >= 0 ? dH : 0) - 2
      ctx.fillStyle = bar.delta >= 0 ? COLORS.delta.pos : COLORS.delta.neg
      ctx.fillRect(x0 + 1, dY, BAR_W - 2, dH || 2)
    })

    // 가격 축 레이블
    ctx.fillStyle   = COLORS.text
    ctx.font        = '10px monospace'
    ctx.textAlign   = 'right'
    for (let i = 0; i <= 5; i++) {
      const p = pMin + (i / 5) * pRange
      const y = PRICE_H - (i / 5) * PRICE_H
      ctx.fillText(p.toFixed(0), 55, y + 3)
    }

  }, [bars])

  return (
    <div className="flex flex-col h-full bg-[#0d1117]">
      <div className="flex items-center gap-2 px-3 py-1.5 bg-[#161b22] border-b border-[#21262d]">
        <span className="text-[#8b949e] text-xs">📊 Footprint — {activeSymbol}</span>
        <button onClick={load} className="ml-auto text-[#484f58] hover:text-[#8b949e] text-xs">↻</button>
      </div>
      <canvas
        ref={canvasRef}
        width={800} height={400}
        className="flex-1 w-full"
        style={{ imageRendering: 'pixelated' }}
      />
      {/* Delta 범례 */}
      <div className="flex gap-3 px-3 py-1 text-[10px] text-[#484f58] border-t border-[#21262d]">
        <span className="text-[#3fb950]">■ 매수</span>
        <span className="text-[#f85149]">■ 매도</span>
        <span style={{color: COLORS.poc}}>■ POC</span>
        <span style={{color: COLORS.imb}}>■ Imbalance</span>
        {bars.length > 0 && (
          <span className="ml-auto text-[#8b949e]">
            델타합: <span style={{color: bars.reduce((s,b)=>s+b.delta,0)>=0?'#3fb950':'#f85149'}}>
              {bars.reduce((s,b)=>s+(b.delta||0),0).toFixed(2)}
            </span>
          </span>
        )}
      </div>
    </div>
  )
}
