import useStore from '../store/useStore'

export default function TopBar({ onBacktest }) {
  const {
    connected, activeSymbol, setActiveSymbol, symbols,
    prices, oi, funding,
    showOrderbook, showScanner, showTimeSales, showFootprint,
    toggleOrderbook, toggleScanner, toggleTimeSales, toggleFootprint,
  } = useStore()

  const price   = prices[activeSymbol]
  const oiVal   = oi[activeSymbol]
  const fundVal = funding[activeSymbol]

  return (
    <div className="flex items-center gap-3 px-4 h-11 bg-[#161b22] border-b border-[#21262d] shrink-0">
      {/* 로고 */}
      <span className="text-[#58a6ff] font-bold text-sm shrink-0">💹 CoinHTS</span>

      {/* 심볼 선택 */}
      <div className="flex gap-1">
        {symbols.map(sym => (
          <button
            key={sym}
            onClick={() => setActiveSymbol(sym)}
            className={`px-2.5 py-1 text-xs rounded transition-colors ${
              activeSymbol === sym
                ? 'bg-[#1f6feb] text-white'
                : 'text-[#8b949e] hover:text-[#c9d1d9] hover:bg-[#21262d]'
            }`}
          >
            {sym.replace('-USDT-SWAP', '')}
          </button>
        ))}
      </div>

      {/* 현재가 */}
      {price && (
        <span className="text-[#c9d1d9] font-mono font-semibold text-sm">
          ${Number(price).toLocaleString('en', { minimumFractionDigits: 1 })}
        </span>
      )}

      {/* OI / 펀딩 */}
      <div className="flex gap-3 text-xs text-[#8b949e]">
        {oiVal   && <span>OI: <span className="text-[#c9d1d9]">{(oiVal/1e6).toFixed(2)}M</span></span>}
        {fundVal !== undefined && (
          <span>
            Funding:
            <span className={fundVal >= 0 ? 'text-[#3fb950]' : 'text-[#f85149]'}>
              {' '}{(fundVal * 100).toFixed(4)}%
            </span>
          </span>
        )}
      </div>

      {/* 패널 토글 */}
      <div className="flex gap-1 ml-auto">
        {[
          ['오더북',   showOrderbook,  toggleOrderbook],
          ['스캐너',   showScanner,    toggleScanner],
          ['T&S',      showTimeSales,  toggleTimeSales],
          ['Footprint',showFootprint,  toggleFootprint],
        ].map(([label, active, toggle]) => (
          <button
            key={label}
            onClick={toggle}
            className={`px-2 py-0.5 text-[10px] rounded border transition-colors ${
              active
                ? 'border-[#1f6feb] text-[#58a6ff] bg-[#1f6feb]/10'
                : 'border-[#30363d] text-[#484f58] hover:text-[#8b949e]'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* 백테스트 버튼 */}
      <button
        onClick={onBacktest}
        className="px-2.5 py-0.5 text-xs bg-[#21262d] hover:bg-[#30363d] text-[#c9d1d9] border border-[#30363d] rounded transition-colors"
      >
        📊 백테스트
      </button>
      {/* 연결 상태 */}
      <div className={`flex items-center gap-1 text-xs shrink-0 ${connected ? 'text-[#3fb950]' : 'text-[#f85149]'}`}>
        <span className="w-1.5 h-1.5 rounded-full" style={{ background: connected ? '#3fb950' : '#f85149' }} />
        {connected ? '연결됨' : '연결 중...'}
      </div>
    </div>
  )
}
