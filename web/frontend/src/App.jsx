import { useWebSocket } from './hooks/useWebSocket'
import useStore from './store/useStore'
import TopBar from './components/TopBar'
import Chart from './components/Chart'
import OrderBook from './components/OrderBook'
import { Scanner, TimeSales, SignalLog } from './components/Panels'

export default function App() {
  useWebSocket()

  const { showOrderbook, showScanner, showTimeSales, showFootprint } = useStore()

  return (
    <div className="flex flex-col h-screen bg-[#0d1117] text-[#c9d1d9] overflow-hidden">
      {/* 상단 툴바 */}
      <TopBar />

      {/* 메인 영역 */}
      <div className="flex flex-1 overflow-hidden">

        {/* 왼쪽 — 메인 차트 */}
        <div className="flex flex-col flex-1 min-w-0">
          {/* 차트 */}
          <div className="flex-1 min-h-0">
            <Chart />
          </div>

          {/* 하단 패널 탭 */}
          <BottomPanel />
        </div>

        {/* 오른쪽 사이드바 */}
        <RightSidebar
          showOrderbook={showOrderbook}
          showScanner={showScanner}
          showTimeSales={showTimeSales}
        />
      </div>
    </div>
  )
}

function BottomPanel() {
  const { showFootprint } = useStore()
  if (!showFootprint) return null

  return (
    <div className="h-48 border-t border-[#21262d] flex">
      <div className="flex-1">
        <SignalLog />
      </div>
    </div>
  )
}

function RightSidebar({ showOrderbook, showScanner, showTimeSales }) {
  if (!showOrderbook && !showScanner && !showTimeSales) return null

  return (
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
  )
}
