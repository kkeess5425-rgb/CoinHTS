import { useState, useEffect } from 'react'

export default function Settings() {
  const [config, setConfig] = useState(null)
  const [saved,  setSaved]  = useState(false)
  const [tab,    setTab]    = useState('trading')

  useEffect(() => {
    fetch('/api/status').then(r => r.json()).then(d => {
      // 기본 설정으로 초기화
      setConfig({
        trading: {
          mode:             'paper',
          risk_per_trade:   1.0,
          max_open:         3,
          max_daily_loss:   3.0,
          min_score:        70.0,
          partial_tp:       true,
          breakeven:        true,
          trailing:         true,
        },
        strategy: {
          min_rr:              2.0,
          min_confluence:      1,
          require_displacement:true,
          swing_length:        10,
          fvg_min_pct:         0.07,
        },
        alert: {
          min_score:      70.0,
          cooldown:       900,
          send_image:     false,
          voice_enabled:  false,
        },
        scanner: {
          volume_mult:    3.0,
          oi_surge_pct:   2.0,
          cooldown:       300,
        },
      })
    }).catch(() => {})
  }, [])

  const upd = (section, key, val) => {
    setConfig(c => ({ ...c, [section]: { ...c[section], [key]: val } }))
    setSaved(false)
  }

  const save = async () => {
    // 실제 환경에서는 POST /api/config 등으로 저장
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  if (!config) return (
    <div className="flex items-center justify-center h-full text-[#484f58] text-xs">
      설정 로딩 중...
    </div>
  )

  const TABS = [
    ['trading',  '💰 자동매매'],
    ['strategy', '🎯 전략'],
    ['alert',    '🔔 알림'],
    ['scanner',  '🔍 스캐너'],
  ]

  const NumInput = ({ label, section, key, step = 0.1, min = 0 }) => (
    <div className="flex items-center justify-between py-1.5 border-b border-[#21262d]/50">
      <label className="text-[10px] text-[#8b949e]">{label}</label>
      <input
        type="number" step={step} min={min}
        value={config[section][key]}
        onChange={e => upd(section, key, parseFloat(e.target.value))}
        className="w-20 bg-[#21262d] text-[#c9d1d9] border border-[#30363d] rounded px-2 py-0.5 text-xs text-right"
      />
    </div>
  )

  const BoolInput = ({ label, section, key }) => (
    <div className="flex items-center justify-between py-1.5 border-b border-[#21262d]/50">
      <label className="text-[10px] text-[#8b949e]">{label}</label>
      <button
        onClick={() => upd(section, key, !config[section][key])}
        className={`w-10 h-5 rounded-full transition-colors relative ${
          config[section][key] ? 'bg-[#1f6feb]' : 'bg-[#21262d]'
        }`}
      >
        <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full transition-transform ${
          config[section][key] ? 'left-5' : 'left-0.5'
        }`} />
      </button>
    </div>
  )

  const SelectInput = ({ label, section, key, options }) => (
    <div className="flex items-center justify-between py-1.5 border-b border-[#21262d]/50">
      <label className="text-[10px] text-[#8b949e]">{label}</label>
      <select
        value={config[section][key]}
        onChange={e => upd(section, key, e.target.value)}
        className="bg-[#21262d] text-[#c9d1d9] border border-[#30363d] rounded px-2 py-0.5 text-xs"
      >
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  )

  return (
    <div className="flex flex-col h-full bg-[#0d1117]">
      {/* 탭 */}
      <div className="flex bg-[#161b22] border-b border-[#21262d] shrink-0">
        {TABS.map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)}
            className={`px-2 py-1.5 text-[10px] border-b-2 transition-colors ${
              tab === key ? 'border-[#1f6feb] text-[#c9d1d9]' : 'border-transparent text-[#484f58] hover:text-[#8b949e]'
            }`}>{label}</button>
        ))}
      </div>

      {/* 설정 내용 */}
      <div className="flex-1 overflow-y-auto px-3 py-2">
        {tab === 'trading' && <>
          <SelectInput label="모드" section="trading" key="mode"
            options={[['paper','페이퍼'],['live','실거래']]} />
          <NumInput label="리스크 (%)" section="trading" key="risk_per_trade" step={0.1} />
          <NumInput label="최대 포지션" section="trading" key="max_open" step={1} />
          <NumInput label="일일 손실 제한 (%)" section="trading" key="max_daily_loss" step={0.5} />
          <NumInput label="최소 점수" section="trading" key="min_score" step={5} />
          <BoolInput label="부분 익절" section="trading" key="partial_tp" />
          <BoolInput label="브레이크이븐" section="trading" key="breakeven" />
          <BoolInput label="트레일링 스탑" section="trading" key="trailing" />
        </>}

        {tab === 'strategy' && <>
          <NumInput label="최소 RR" section="strategy" key="min_rr" step={0.5} />
          <NumInput label="최소 컨플루언스" section="strategy" key="min_confluence" step={1} />
          <BoolInput label="Displacement 필요" section="strategy" key="require_displacement" />
          <NumInput label="스윙 길이" section="strategy" key="swing_length" step={1} />
          <NumInput label="FVG 최소 (%)" section="strategy" key="fvg_min_pct" step={0.01} />
        </>}

        {tab === 'alert' && <>
          <NumInput label="최소 점수" section="alert" key="min_score" step={5} />
          <NumInput label="쿨다운 (초)" section="alert" key="cooldown" step={60} />
          <BoolInput label="차트 이미지" section="alert" key="send_image" />
          <BoolInput label="음성 알림" section="alert" key="voice_enabled" />
        </>}

        {tab === 'scanner' && <>
          <NumInput label="볼륨 배수" section="scanner" key="volume_mult" step={0.5} />
          <NumInput label="OI 급증 (%)" section="scanner" key="oi_surge_pct" step={0.5} />
          <NumInput label="쿨다운 (초)" section="scanner" key="cooldown" step={60} />
        </>}
      </div>

      {/* 저장 버튼 */}
      <div className="px-3 py-2 border-t border-[#21262d] shrink-0">
        <button
          onClick={save}
          className={`w-full py-1.5 rounded text-xs transition-colors ${
            saved ? 'bg-[#238636] text-white' : 'bg-[#21262d] text-[#c9d1d9] hover:bg-[#30363d]'
          }`}
        >
          {saved ? '✅ 저장됨' : '저장'}
        </button>
      </div>
    </div>
  )
}
