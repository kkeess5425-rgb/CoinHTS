import { useState } from 'react'
import { useWebSocket } from './hooks/useWebSocket'
import useStore from './store/useStore'
import TopBar from './components/TopBar'
import Chart from './components/Chart'
import OrderBook from './components/OrderBook'
import { Scanner, TimeSales, SignalLog } from './components/Panels'
import Backtest from './components/Backtest'
import FootprintChart from './components/FootprintChart'
import { StatsPanel, WhalePanel, NewsPanel, JournalPanel } from './components/Dashboard'
import AISummary from './components/AISummary'
import PositionsPanel from './components/Positions'
import GridSearch from './components/GridSearch'
import EquityCurve from './components/EquityCurve'

const BOTTOM_TABS = [
  ['signals',    '🚀 신호'],
  ['footprint',  '📊 Footprint'],
  ['stats',      '📈 통계'],
  ['journal',    '📋 일지'],
  ['ai',         '🤖 AI 요약'],
  ['equity',     '📈 Equity'],
  ['gridsearch', '⚡ 최적화'],
]
const RIGHT_TABS = [
  ['orderbook',  '호가창'],
  ['scanner',    '스캐너'],
  ['timesales',  'T&S'],
  ['whale',      '🐋 고래'],
  ['news',       '📰 뉴스'],
  ['positions',  '💼 포지션'],
]

export default function App() {
  useWebSocket()
  const [showBacktest, setShowBacktest] = useState(false)
  const [bottomTab,    setBottomTab]    = useState('signals')
  const [rightTab,     setRightTab]     = useState('orderbook')

  return (
    <div className="flex flex-col h-screen bg-[#0d1117] text-[#c9d1d9] overflow-hidden">
      <TopBar onBacktest={() => setShowBacktest(true)} />

      <div className="flex flex-1 min-h-0">
        {/* 왼쪽 — 차트 + 하단 탭 */}
        <div className="flex flex-col flex-1 min-w-0">
          <div className="flex-1 min-h-0">
            <Chart />
          </div>

          {/* 하단 탭 */}
          <div className="h-52 border-t border-[#21262d] flex flex-col shrink-0">
            <div className="flex bg-[#161b22] border-b border-[#21262d] shrink-0">
              {BOTTOM_TABS.map(([key, label]) => (
                <button key={key} onClick={() => setBottomTab(key)}
                  className={`px-3 py-1.5 text-xs border-b-2 transition-colors ${
                    bottomTab === key
                      ? 'border-[#1f6feb] text-[#c9d1d9]'
                      : 'border-transparent text-[#484f58] hover:text-[#8b949e]'
                  }`}>{label}</button>
              ))}
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
              {bottomTab === 'signals'   && <SignalLog />}
              {bottomTab === 'footprint' && <FootprintChart />}
              {bottomTab === 'stats'     && <StatsPanel />}
              {bottomTab === 'journal'   && <JournalPanel />}
              {bottomTab === 'ai'        && <AISummary />}
              {bottomTab === 'equity'    && <EquityCurve />}
              {bottomTab === 'gridsearch'&& <GridSearch />}
            </div>
          </div>
        </div>

        {/* 오른쪽 사이드바 */}
        <div className="w-60 flex flex-col border-l border-[#21262d] shrink-0">
          {/* 탭 선택 */}
          <div className="flex flex-wrap bg-[#161b22] border-b border-[#21262d] shrink-0">
            {RIGHT_TABS.map(([key, label]) => (
              <button key={key} onClick={() => setRightTab(key)}
                className={`px-2 py-1 text-[10px] border-b-2 transition-colors ${
                  rightTab === key
                    ? 'border-[#1f6feb] text-[#c9d1d9]'
                    : 'border-transparent text-[#484f58] hover:text-[#8b949e]'
                }`}>{label}</button>
            ))}
          </div>
          <div className="flex-1 min-h-0 overflow-hidden">
            {rightTab === 'orderbook' && <OrderBook />}
            {rightTab === 'scanner'   && <Scanner />}
            {rightTab === 'timesales' && <TimeSales />}
            {rightTab === 'whale'     && <WhalePanel />}
            {rightTab === 'news'      && <NewsPanel />}
            {rightTab === 'positions'  && <PositionsPanel />}
          </div>
        </div>
      </div>

      {showBacktest && <Backtest onClose={() => setShowBacktest(false)} />}
    </div>
  )
}
