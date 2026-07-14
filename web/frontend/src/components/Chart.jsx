import { useEffect, useRef, useState, useCallback } from 'react'
import { createChart, ColorType, CrosshairMode } from 'lightweight-charts'
import useStore from '../store/useStore'

const TF_OPTIONS = ['1m', '3m', '5m', '15m', '1H', '4H']

export default function Chart() {
  const containerRef = useRef(null)
  const chartRef     = useRef(null)
  const candleRef    = useRef(null)
  const ema20Ref     = useRef(null)
  const ema50Ref     = useRef(null)
  const volRef       = useRef(null)

  const { activeSymbol, timeframe, setTimeframe } = useStore()
  const [loading, setLoading]   = useState(false)
  const [candleCount, setCandleCount] = useState(0)
  const [crosshairPrice, setCrosshairPrice] = useState(null)

  // 차트 초기화
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#0d1117' },
        textColor:  '#8b949e',
      },
      grid: {
        vertLines:   { color: '#21262d' },
        horzLines:   { color: '#21262d' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#30363d' },
      timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: false },
      width:  containerRef.current.clientWidth,
      height: containerRef.current.clientHeight - 80,
    })

    const candleSeries = chart.addCandlestickSeries({
      upColor:        '#26a641', downColor:      '#f85149',
      borderUpColor:  '#26a641', borderDownColor:'#f85149',
      wickUpColor:    '#26a641', wickDownColor:  '#f85149',
    })
    const ema20Series = chart.addLineSeries({ color: '#e3b341', lineWidth: 1 })
    const ema50Series = chart.addLineSeries({ color: '#bc8cff', lineWidth: 1 })

    // 크로스헤어 가격 표시
    chart.subscribeCrosshairMove((param) => {
      if (param.point && candleSeries) {
        const price = param.seriesData?.get(candleSeries)?.close
        if (price) setCrosshairPrice(price.toFixed(2))
      }
    })

    // 왼쪽 스크롤 → 과거 데이터 로드
    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (range && range.from < 10) loadMoreHistory()
    })

    chartRef.current    = chart
    candleRef.current   = candleSeries
    ema20Ref.current    = ema20Series
    ema50Ref.current    = ema50Series

    const ro = new ResizeObserver(() => {
      if (containerRef.current)
        chart.resize(containerRef.current.clientWidth, containerRef.current.clientHeight - 80)
    })
    ro.observe(containerRef.current)

    return () => { chart.remove(); ro.disconnect() }
  }, [])

  // 캔들 로드
  const loadCandles = useCallback(async (before = null) => {
    setLoading(true)
    try {
      const url = `/api/candles?symbol=${encodeURIComponent(activeSymbol)}&timeframe=${timeframe}&limit=300${before ? `&before=${before}` : ''}`
      const res  = await fetch(url)
      const data = await res.json()
      if (!data.candles?.length) return null

      const candles = data.candles.map(c => ({
        time:  c.t, open: c.o, high: c.h, low: c.l, close: c.c,
      }))
      const e20 = (data.ema20 || []).map((v, i) => ({ time: data.candles[i]?.t, value: v })).filter(x => x.value)
      const e50 = (data.ema50 || []).map((v, i) => ({ time: data.candles[i]?.t, value: v })).filter(x => x.value)

      candleRef.current?.setData(candles)
      ema20Ref.current?.setData(e20)
      ema50Ref.current?.setData(e50)
      setCandleCount(candles.length)
      chartRef.current?.timeScale().fitContent()
      return data.candles[0]?.t
    } catch (e) {
      console.error('캔들 로드 오류:', e)
    } finally {
      setLoading(false)
    }
    return null
  }, [activeSymbol, timeframe])

  const oldestTs = useRef(null)
  const loadingMore = useRef(false)

  const loadMoreHistory = useCallback(async () => {
    if (loadingMore.current || !oldestTs.current) return
    loadingMore.current = true
    try {
      const url = `/api/candles?symbol=${encodeURIComponent(activeSymbol)}&timeframe=${timeframe}&limit=300&before=${oldestTs.current}`
      const res  = await fetch(url)
      const data = await res.json()
      if (!data.candles?.length) return

      const existing = candleRef.current?.data?.() || []
      const newCandles = data.candles.map(c => ({ time:c.t, open:c.o, high:c.h, low:c.l, close:c.c }))
      const merged = [...newCandles, ...existing]
      const seen   = new Set()
      const deduped = merged.filter(c => { if(seen.has(c.time)) return false; seen.add(c.time); return true })
      deduped.sort((a,b) => a.time - b.time)
      candleRef.current?.setData(deduped)
      oldestTs.current = data.candles[0]?.t
      setCandleCount(deduped.length)
    } finally {
      loadingMore.current = false
    }
  }, [activeSymbol, timeframe])

  // 심볼/TF 변경 시 리로드
  useEffect(() => {
    loadCandles().then(ts => { oldestTs.current = ts })
  }, [activeSymbol, timeframe])

  return (
    <div className="flex flex-col h-full bg-[#0d1117]">
      {/* 헤더 */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-[#161b22] border-b border-[#21262d]">
        <span className="text-[#c9d1d9] font-semibold text-sm">{activeSymbol}</span>
        {crosshairPrice && (
          <span className="text-[#8b949e] text-xs">{crosshairPrice}</span>
        )}
        <div className="flex gap-1 ml-auto">
          {TF_OPTIONS.map(tf => (
            <button
              key={tf}
              onClick={() => setTimeframe(tf)}
              className={`px-2 py-0.5 text-xs rounded transition-colors ${
                timeframe === tf
                  ? 'bg-[#1f6feb] text-white'
                  : 'text-[#8b949e] hover:text-[#c9d1d9] hover:bg-[#21262d]'
              }`}
            >
              {tf}
            </button>
          ))}
        </div>
        {loading && <span className="text-[#8b949e] text-xs animate-pulse">로딩중...</span>}
        <span className="text-[#484f58] text-xs">{candleCount}봉</span>
      </div>

      {/* 차트 영역 */}
      <div ref={containerRef} className="flex-1 relative">
        {/* EMA 범례 */}
        <div className="absolute top-2 left-2 flex gap-3 text-xs z-10 pointer-events-none">
          <span className="text-[#e3b341]">● EMA20</span>
          <span className="text-[#bc8cff]">● EMA50</span>
        </div>
      </div>
    </div>
  )
}
