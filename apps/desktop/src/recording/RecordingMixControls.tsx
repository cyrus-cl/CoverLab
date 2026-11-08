import * as RadioGroup from "@radix-ui/react-radio-group";
import * as Slider from "@radix-ui/react-slider";

import type { MixSettings } from "../api/recordingMix";

const PREPARATION_OPTIONS = [
  { value: "instrumental", label: "伴奏" },
  { value: "mixed", label: "混合" },
  { value: "vocals", label: "人声" },
] as const;

type Props = {
  settings: MixSettings;
  hasSeparatedStems: boolean;
  disabled: boolean;
  onChange: (settings: MixSettings) => void;
};

export function RecordingMixControls({ settings, hasSeparatedStems, disabled, onChange }: Props) {
  const backingDisabled = disabled || !hasSeparatedStems || !settings.includeBacking;

  return (
    <fieldset className="recording-mix" disabled={disabled}>
      <legend>背景音</legend>
      <label className="camera-toggle recording-mix__toggle">
        <input
          type="checkbox"
          checked={hasSeparatedStems && settings.includeBacking}
          disabled={!hasSeparatedStems}
          onChange={event => onChange({ ...settings, includeBacking: event.target.checked })}
        />
        混入背景音
      </label>
      {!hasSeparatedStems ? <p className="recording-mix__hint">生成伴奏与人声后可混入背景音。</p> : null}
      <MixSlider
        label="准备时长"
        value={settings.preparationSeconds}
        max={10}
        unit=" 秒"
        disabled={disabled}
        onChange={preparationSeconds => onChange({ ...settings, preparationSeconds })}
      />
      <div className="recording-mix__source">
        <span>准备音源</span>
        <RadioGroup.Root
          aria-label="准备音源"
          className="recording-mix__modes"
          orientation="horizontal"
          value={settings.preparationAudio}
          disabled={backingDisabled}
          onValueChange={value => {
            const mode = PREPARATION_OPTIONS.find(option => option.value === value);
            if (mode) onChange({ ...settings, preparationAudio: mode.value });
          }}
        >
          {PREPARATION_OPTIONS.map(({ value, label }) => (
            <RadioGroup.Item className="recording-mix__mode" key={value} value={value}>
              {label}
            </RadioGroup.Item>
          ))}
        </RadioGroup.Root>
      </div>
      <MixSlider
        label="人声音量"
        value={settings.vocalPercent}
        max={100}
        unit="%"
        disabled={backingDisabled}
        onChange={vocalPercent => onChange({ ...settings, vocalPercent })}
      />
    </fieldset>
  );
}

function MixSlider({ label, value, max, unit, disabled, onChange }: {
  label: string;
  value: number;
  max: number;
  unit: string;
  disabled: boolean;
  onChange: (value: number) => void;
}) {
  const displayValue = value + unit;

  return (
    <div className="recording-mix__level">
      <div className="recording-mix__readout">
        <span>{label}</span>
        <output aria-hidden="true">{displayValue}</output>
      </div>
      <Slider.Root
        className="recording-mix__slider"
        min={0}
        max={max}
        step={1}
        value={[value]}
        disabled={disabled}
        onValueChange={([next]) => onChange(next)}
      >
        <Slider.Track className="recording-mix__track">
          <Slider.Range className="recording-mix__fill" />
        </Slider.Track>
        <Slider.Thumb
          className="recording-mix__thumb"
          aria-label={label}
          aria-valuetext={displayValue}
          aria-disabled={disabled || undefined}
        />
      </Slider.Root>
    </div>
  );
}
