import { useState, useEffect } from 'react'
import useStore from '../store/useStore'

// ── 통계 패널 ─────────────────────────────────────────
export function StatsPanel() {
  const [stats, setStats] = useState(null)
  const { activeSymbol }  = useStore()

  useEffect(() => {
    fetch(`/api/stats?symbol=${encodeURIComponent(activeSymbol)}`)
      .then(r => r.json()).then(setStats).catch(() => {})
  }, [activeSymbol])

  if (!stats || !stats.total_trades) return (
    <div className="h-full flex items-center justify-center text-[#484f58] text-xs">
      트레이드 데이터 없음
    </div>
  )

  const items = [
    ['총 트레이드', stats.total_trades],
    ['승률',        `${stats.win_rate?.toFixed(1)}%`],
    ['Profit Factor', stats.profit_factor?.toFixed(2)],
    ['기대값',      `${stats.expectancy > 0 ? '+' : ''}${stats.expectancy?.toFixed(3)}R`],
    ['Sharpe',      stats.sharpe_ratio?.toFixed(2)],
    ['Sortino',     stats.sortino_ratio?.toFixed(2)],
    ['MDD',         `${stats.max_drawdown?.toFixed(1)}%`],
    ['최대연속손실', `${stats.max_consec_losses}회`],
    ['평균보유',    `${stats.avg_hold_minutes?.toFixed(0)}분`],
  ]

  return (
    <div className="flex flex-col h-full bg-[#0d1117]">
      <div className="px-3 py-1.5 bg-[#161b22] border-b border-[#21262d] text-[#8b949e] text-xs">
        📈 통계
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {items.map(([label, val], i) => (
          <div key={i} className="flex justify-between text-xs py-0.5 border-b border-[#21262d]/30">
            <span className="text-[#8b949e]">{label}</span>
            <span className="text-[#c9d1d9] font-mono">{val}</span>
          </div>
        ))}
        {/* 요일별 승률 */}
        {stats.daily_wr && Object.keys(stats.daily_wr).length > 0 && (
          <div className="mt-2">
            <div className="text-[#484f58] text-[10px] mb-1">요일별 승률</div>
            <div className="flex gap-1">
              {Object.entries(stats.daily_wr).map(([day, wr]) => (
                <div key={day} className="flex-1 text-center">
                  <div className="text-[9px] text-[#484f58]">{day}</div>
                  <div className="h-8 relative bg-[#21262d] rounded overflow-hidden mt-0.5">
                    <div
                      className="absolute bottom-0 left-0 right-0"
                      style={{
                        height: `${wr}%`,
                        background: wr >= 55 ? '#3fb950' : wr >= 45 ? '#e3b341' : '#f85149'
                      }}
                    />
                  </div>
                  <div className="text-[9px] text-[#8b949e] mt-0.5">{wr?.toFixed(0)}%</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── 고래 추적 패널 ────────────────────────────────────
export function WhalePanel() {
  const [data, setData] = useState(null)

  useEffect(() => {
    const load = () => fetch('/api/whale').then(r => r.json()).then(setData).catch(() => {})
    load()
    const t = setInterval(load, 30000)
    return () => clearInterval(t)
  }, [])

  const kindIcon = {
    exchange_inflow:  ['↓', '#f85149', '거래소 유입'],
    exchange_outflow: ['↑', '#3fb950', '거래소 유출'],
    wallet_to_wallet: ['→', '#8b949e', '지갑 이동'],
  }

  return (
    <div className="flex flex-col h-full bg-[#0d1117]">
      <div className="px-3 py-1.5 bg-[#161b22] border-b border-[#21262d] text-[#8b949e] text-xs flex justify-between">
        <span>🐋 고래 추적</span>
        {data?.sentiment && (
          <span style={{ color: data.sentiment.signal === 'bullish' ? '#3fb950' : '#f85149' }}>
            {data.sentiment.signal === 'bullish' ? '매수 우세' : '매도 압력'}
          </span>
        )}
      </div>
      <div className="flex-1 overflow-y-auto">
        {(!data?.transfers || data.transfers.length === 0) ? (
          <div className="text-[#484f58] text-xs text-center mt-4">데이터 로딩 중...</div>
        ) : data.transfers.slice(0, 10).map((t, i) => {
          const [icon, color, label] = kindIcon[t.kind] || ['?', '#8b949e', t.kind]
          const usd = t.usd_value ? `$${(t.usd_value/1e6).toFixed(1)}M` : ''
          const ts  = new Date(t.ts * 1000).toLocaleTimeString('ko-KR')
          return (
            <div key={i} className="px-3 py-1.5 border-b border-[#21262d]/50 hover:bg-[#161b22]">
              <div className="flex items-center gap-1.5 text-xs">
                <span style={{ color }}>{icon}</span>
                <span className="text-[#8b949e] text-[10px]">{label}</span>
                <span className="font-mono text-[#c9d1d9] ml-auto">{usd}</span>
                <span className="text-[#484f58] text-[10px]">{ts}</span>
              </div>
              <div className="text-[10px] text-[#484f58] mt-0.5 truncate">
                {t.from_addr} → {t.to_addr}
              </div>
            </div>
          )
        })}
        {/* 거래소 순유입 */}
        {data?.exchange_flows && data.exchange_flows.length > 0 && (
          <div className="px-3 py-2 border-t border-[#21262d]">
            <div className="text-[#484f58] text-[10px] mb-1">거래소 순유입</div>
            {data.exchange_flows.slice(0, 3).map((f, i) => (
              <div key={i} className="flex justify-between text-[10px] py-0.5">
                <span className="text-[#8b949e]">{f.exchange}</span>
                <span style={{ color: f.netflow > 0 ? '#f85149' : '#3fb950' }}>
                  {f.netflow > 0 ? '+' : ''}{f.netflow?.toFixed(0)} BTC
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── 뉴스 / 경제 캘린더 패널 ───────────────────────────
export function NewsPanel() {
  const [data, setData] = useState(null)

  useEffect(() => {
    const load = () => fetch('/api/news').then(r => r.json()).then(setData).catch(() => {})
    load()
    const t = setInterval(load, 300000)
    return () => clearInterval(t)
  }, [])

  const impactColor = { high: '#f85149', medium: '#e3b341', low: '#484f58' }
  const impactIcon  = { high: '🔴', medium: '🟡', low: '⚪' }

  return (
    <div className="flex flex-col h-full bg-[#0d1117]">
      <div className="px-3 py-1.5 bg-[#161b22] border-b border-[#21262d] text-[#8b949e] text-xs">
        📰 뉴스 / 경제 캘린더
      </div>
      <div className="flex-1 overflow-y-auto">
        {/* AI 요약 */}
        {data?.ai_summary && (
          <div className="px-3 py-2 border-b border-[#21262d] bg-[#161b22]/50">
            <div className="text-[10px] text-[#58a6ff]">🤖 AI 요약</div>
            <div className="text-[10px] text-[#8b949e] mt-0.5">{data.ai_summary}</div>
          </div>
        )}
        {/* 경제 이벤트 */}
        {data?.events?.map((e, i) => {
          const dt  = new Date(e.ts * 1000)
          const str = `${dt.getMonth()+1}/${dt.getDate()} ${dt.getHours()}:${String(dt.getMinutes()).padStart(2,'0')}`
          return (
            <div key={i} className="px-3 py-1.5 border-b border-[#21262d]/50 hover:bg-[#161b22]">
              <div className="flex items-center gap-1.5">
                <span className="text-[10px]">{impactIcon[e.impact]}</span>
                <span className="text-xs text-[#c9d1d9] flex-1 truncate">{e.title}</span>
                <span className="text-[10px] text-[#484f58] shrink-0">{str}</span>
              </div>
              {(e.forecast || e.previous) && (
                <div className="flex gap-2 text-[10px] text-[#484f58] mt-0.5">
                  {e.forecast  && <span>예측: <span className="text-[#8b949e]">{e.forecast}</span></span>}
                  {e.previous  && <span>이전: <span className="text-[#8b949e]">{e.previous}</span></span>}
                  {e.actual    && <span>실제: <span className="text-[#3fb950]">{e.actual}</span></span>}
                </div>
              )}
            </div>
          )
        })}
        {(!data?.events || data.events.length === 0) && (
          <div className="text-[#484f58] text-xs text-center mt-4">
            24시간 내 주요 이벤트 없음
          </div>
        )}
      </div>
    </div>
  )
}

// ── 매매일지 패널 ─────────────────────────────────────
export function JournalPanel() {
  const [data, setData] = useState(null)
  const [tab,  setTab]  = useState('trades')  // 'trades' | 'mistakes'

  useEffect(() => {
    const load = () => fetch('/api/journal').then(r => r.json()).then(setData).catch(() => {})
    load()
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="flex flex-col h-full bg-[#0d1117]">
      <div className="flex bg-[#161b22] border-b border-[#21262d]">
        {[['trades','📋 일지'],['mistakes','⚠️ 실수']].map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)}
            className={`px-3 py-1.5 text-xs border-b-2 transition-colors ${
              tab === key ? 'border-[#1f6feb] text-[#c9d1d9]' : 'border-transparent text-[#484f58] hover:text-[#8b949e]'
            }`}>{label}</button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto">
        {tab === 'trades' && (
          <>
            {(!data?.entries || data.entries.length === 0) ? (
              <div className="text-[#484f58] text-xs text-center mt-4">매매 내역 없음</div>
            ) : data.entries.slice().reverse().slice(0,20).map((e, i) => {
              const win = e.pnl_r > 0
              const ts  = new Date(e.entry_ts * 1000).toLocaleDateString('ko-KR')
              return (
                <div key={i} className="px-3 py-2 border-b border-[#21262d]/50 hover:bg-[#161b22]">
                  <div className="flex items-center gap-2 text-xs">
                    <span>{win ? '✅' : e.pnl_r < 0 ? '❌' : '⚖️'}</span>
                    <span className={win ? 'text-[#3fb950]' : 'text-[#f85149]'}>
                      {e.direction} {e.symbol?.replace('-USDT-SWAP','')}
                    </span>
                    <span className="text-[#484f58] text-[10px] ml-auto">{ts}</span>
                  </div>
                  <div className="text-[10px] text-[#8b949e] mt-0.5">
                    {e.entry?.toFixed(0)} → {e.exit_price?.toFixed(0)} ({e.exit_reason})
                    <span className={`ml-2 font-mono ${win ? 'text-[#3fb950]' : 'text-[#f85149]'}`}>
                      {e.pnl_r > 0 ? '+' : ''}{e.pnl_r?.toFixed(2)}R
                    </span>
                  </div>
                  {e.mistakes?.length > 0 && (
                    <div className="text-[10px] text-[#e3b341] mt-0.5">
                      ⚠️ {e.mistakes.map(m => m.kind).join(', ')}
                    </div>
                  )}
                </div>
              )
            })}
          </>
        )}

        {tab === 'mistakes' && (
          <div className="p-3 space-y-2">
            {(!data?.mistake_stats || Object.keys(data.mistake_stats).length === 0) ? (
              <div className="text-[#484f58] text-xs text-center mt-4">감지된 실수 없음 👍</div>
            ) : Object.entries(data.mistake_stats).map(([kind, count], i) => {
              const labels = {
                chasing:    '추격매수',
                revenge:    '복수매매',
                fomo:       '과매매(FOMO)',
                bad_rr:     'RR 부족',
                wide_sl:    'SL 과다',
              }
              return (
                <div key={i} className="flex justify-between items-center bg-[#161b22] rounded px-3 py-2">
                  <div>
                    <div className="text-xs text-[#c9d1d9]">{labels[kind] || kind}</div>
                    <div className="text-[10px] text-[#484f58]">{kind}</div>
                  </div>
                  <div className="text-sm font-bold text-[#f85149]">{count}회</div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
