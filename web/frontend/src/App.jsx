import { useState } from 'react'
import { useWebSocket } from './hooks/useWebSocket'
import useStore from './store/useStore'
import TopBar from './components/TopBar'
import Chart from './components/Chart'
import OrderBook from './components/OrderBook'
import { Scanner, TimeSales, SignalLog } from './components/Panels'
import Backtest from './components/Backtest'
import FootprintChart from './components/FootprintChart'

export default function App() {
  useWebSocket()
  const [showBacktest,  setShowBacktest]  = useState(false)
  const [bottomTab,     setBottomTab]     = useState('signals') // 'signals' | 'footprint'
  const { showOrderbook, showScanner, showTimeSales } = useStore()

  return (
    <div className="flex flex-col h-screen bg-[#0d1117] text-[#c9d1d9] overflow-hidden">
      {/* 상단 툴바 */}
      <TopBar onBacktest={() => setShowBacktest(true)} />

      {/* 메인 영역 */}
      <div className="flex flex-1 min-h-0">
        {/* 왼쪽 — 차트 + 하단 패널 */}
        <div className="flex flex-col flex-1 min-w-0">
          {/* 차트 */}
          <div className="flex-1 min-h-0">
            <Chart />
          </div>

          {/* 하단 탭 패널 */}
          <div className="h-48 border-t border-[#21262d] flex flex-col shrink-0">
            {/* 탭 선택 */}
            <div className="flex gap-0 bg-[#161b22] border-b border-[#21262d]">
              {[['signals','🚀 신호'], ['footprint','📊 Footprint']].map(([key, label]) => (
                <button
                  key={key}
                  onClick={() => setBottomTab(key)}
                  className={`px-4 py-1.5 text-xs border-b-2 transition-colors ${
                    bottomTab === key
                      ? 'border-[#1f6feb] text-[#c9d1d9]'
                      : 'border-transparent text-[#484f58] hover:text-[#8b949e]'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
              {bottomTab === 'signals'    && <SignalLog />}
              {bottomTab === 'footprint'  && <FootprintChart />}
            </div>
          </div>
        </div>

        {/* 오른쪽 사이드바 */}
        {(showOrderbook || showScanner || showTimeSales) && (
          <div className="w-56 flex flex-col border-l border-[#21262d] shrink-0">
            {showOrderbook && (
              <div className="flex-1 border-b border-[#21262d] min-h-0 overflow-hidden">
                <OrderBook />
              </div>
            )}
            {showScanner && (
              <div className="flex-1 border-b border-[#21262d] min-h-0 overflow-hidden">
                <Scanner />
              </div>
            )}
            {showTimeSales && (
              <div className="flex-1 min-h-0 overflow-hidden">
                <TimeSales />
              </div>
            )}
          </div>
        )}
      </div>

      {/* 백테스트 모달 */}
      {showBacktest && <Backtest onClose={() => setShowBacktest(false)} />}
    </div>
  )
}
