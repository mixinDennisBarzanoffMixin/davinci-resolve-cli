import React from 'react';
import {Video} from '@remotion/media';
import {
  AbsoluteFill,
  Easing,
  Img,
  Interactive,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import type {
  BrollAsset,
  BrollPlacement,
  MotionVariant,
  SeededMotion,
  SegmentProps,
} from './types';

type SceneKind =
  | 'source_cutaway'
  | 'generated_illustration'
  | 'evidence_image'
  | 'motion_graphic'
  | 'diagram';

const assetSource = (src: string) =>
  src.startsWith('http://') || src.startsWith('https://')
    ? src
    : staticFile(src.replace(/^\//, ''));

const stableHash = (value: string): number => {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index++) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
};

const seededUnit = (seed: number, salt: number): number => {
  let value = (seed + Math.imul(salt + 1, 0x9e3779b1)) >>> 0;
  value ^= value >>> 16;
  value = Math.imul(value, 0x7feb352d);
  value ^= value >>> 15;
  value = Math.imul(value, 0x846ca68b);
  value ^= value >>> 16;
  return (value >>> 0) / 4294967295;
};

const seedFor = (placement: BrollPlacement): number => {
  const configured = placement.treatment?.motion?.seed
    ?? placement.treatment?.seed
    ?? placement.motion_seed;
  return stableHash(String(configured ?? `${placement.id}:${placement.beat_id ?? ''}`));
};

const clampIntensity = (motion?: SeededMotion): number =>
  Math.max(0, Math.min(1, motion?.intensity ?? 0.55));

const inferSceneKind = (placement: BrollPlacement): SceneKind => {
  if (placement.treatment?.kind) {
    return placement.treatment.kind === 'generated_image'
      ? 'generated_illustration'
      : placement.treatment.kind;
  }
  const origin = placement.asset?.origin;
  if (origin === 'project_source' || origin === 'source_cutaway') {
    return 'source_cutaway';
  }
  if (origin === 'generated' || origin === 'ai_generated') {
    return 'generated_illustration';
  }
  if (placement.visual_type === 'generated_image') {
    return 'generated_illustration';
  }
  if (placement.visual_type === 'source_cutaway') {
    return 'source_cutaway';
  }
  if (placement.asset?.kind === 'video') {
    return 'source_cutaway';
  }
  if (placement.asset?.kind === 'image') {
    return 'evidence_image';
  }
  return placement.visual_type === 'diagram' ? 'diagram' : 'motion_graphic';
};

const resolvedMotionVariant = (
  placement: BrollPlacement,
  kind: SceneKind,
): MotionVariant => {
  const requested = placement.treatment?.motion?.variant;
  if (requested && requested !== 'auto') {
    return requested;
  }
  if (kind === 'source_cutaway') {
    return 'static';
  }
  const variants: MotionVariant[] = [
    'push-in',
    'pull-out',
    'pan-left',
    'pan-right',
    'drift-up',
    'drift-down',
  ];
  return variants[Math.floor(seededUnit(seedFor(placement), 0) * variants.length)] ?? 'push-in';
};

const mediaMotionStyle = (
  placement: BrollPlacement,
  kind: SceneKind,
  frame: number,
  durationInFrames: number,
): React.CSSProperties => {
  const variant = resolvedMotionVariant(placement, kind);
  const intensity = clampIntensity(placement.treatment?.motion);
  const endFrame = Math.max(1, durationInFrames - 1);
  const progress = [0, endFrame];
  const scaleDelta = 0.035 + intensity * 0.045;
  const travel = 1.5 + intensity * 3.5;
  const base: React.CSSProperties = {
    width: '100%',
    height: '100%',
    objectFit: 'cover',
  };

  if (variant === 'static') {
    return base;
  }

  const easing = Easing.bezier(0.16, 1, 0.3, 1);
  if (variant === 'push-in' || variant === 'pull-out') {
    const values = variant === 'push-in'
      ? [1.01, 1.01 + scaleDelta]
      : [1.01 + scaleDelta, 1.01];
    return {
      ...base,
      scale: interpolate(frame, progress, values, {
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
        easing,
        output: 'perceptual-scale',
      }),
    };
  }

  const translateValues: [string, string] = variant === 'pan-left'
    ? [`${travel}% 0%`, `${-travel}% 0%`]
    : variant === 'pan-right'
      ? [`${-travel}% 0%`, `${travel}% 0%`]
      : variant === 'drift-up'
        ? [`0% ${travel}%`, `0% ${-travel}%`]
        : [`0% ${-travel}%`, `0% ${travel}%`];
  return {
    ...base,
    scale: 1.01 + scaleDelta,
    translate: interpolate(frame, progress, translateValues, {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
      easing,
    }),
  };
};

const trimFrames = (asset: BrollAsset, fps: number) => {
  const before = asset.trimBefore
    ?? asset.trim_before_frames
    ?? Math.round((asset.source_in_seconds ?? asset.trim_before_seconds ?? 0) * fps);
  const afterSeconds = asset.source_out_seconds ?? asset.trim_after_seconds;
  const after = asset.trimAfter
    ?? asset.trim_after_frames
    ?? (afterSeconds === undefined ? undefined : Math.round(afterSeconds * fps));
  return {
    trimBefore: Math.max(0, before),
    trimAfter: after === undefined ? undefined : Math.max(0, after),
  };
};

const disclosureFor = (placement: BrollPlacement, kind: SceneKind): string => {
  const asset = placement.asset;
  if (kind === 'source_cutaway') {
    return asset?.exact_item === false
      ? 'Кадър от проекта · илюстративен обект'
      : 'Кадър от проекта · точният заснет обект';
  }
  if (kind === 'generated_illustration') {
    return 'AI илюстрация · не е точният обект';
  }
  if (asset?.exact_item) {
    return 'Точният обект · одобрен източник';
  }
  return kind === 'diagram'
    ? 'Илюстративна схема · провери спецификацията'
    : 'Илюстративна графика · провери оборудването';
};

const GraphicScene: React.FC<{
  placement: BrollPlacement;
  accentColor: string;
}> = ({placement, accentColor}) => {
  const frame = useCurrentFrame();
  const {durationInFrames, fps} = useVideoConfig();
  const seed = seedFor(placement);
  const graphic = placement.treatment?.kind === 'motion_graphic'
    || placement.treatment?.kind === 'diagram'
    ? placement.treatment.graphic
    : undefined;
  const eyebrow = graphic?.eyebrow || (placement.visual_type === 'diagram' ? 'DIAGRAM' : 'DETAIL');
  const primary = graphic?.primary_text || '';
  const secondary = graphic?.secondary_text || '';
  const motifs = ['rings', 'grid', 'pulse'] as const;
  const motif = graphic?.motif || motifs[Math.floor(seededUnit(seed, 5) * motifs.length)] || 'rings';
  const horizontal = 49 + seededUnit(seed, 1) * 30;
  const vertical = 18 + seededUnit(seed, 2) * 28;
  const endFrame = Math.max(1, durationInFrames - 1);
  const fadeInEnd = Math.min(endFrame, Math.max(8, Math.round(fps * 0.45)));
  const fadeOutStart = Math.max(fadeInEnd, endFrame - Math.max(8, Math.round(fps * 0.35)));

  return (
    <AbsoluteFill
      name="Evidence-safe graphic"
      style={{
        background: `radial-gradient(circle at ${horizontal}% ${vertical}%, ${accentColor}55 0%, #15171b 36%, #070708 100%)`,
      }}
    >
      <Interactive.Div
        name="Graphic orbit"
        style={{
          position: 'absolute',
          width: motif === 'pulse' ? 820 : 680,
          height: motif === 'pulse' ? 360 : 680,
          borderRadius: motif === 'rings' ? 999 : motif === 'grid' ? 70 : 180,
          border: `10px solid ${accentColor}`,
          opacity: interpolate(frame, [0, fadeInEnd, fadeOutStart, endFrame], [0, 0.38, 0.38, 0], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
            easing: Easing.bezier(0.16, 1, 0.3, 1),
          }),
          scale: interpolate(frame, [0, endFrame], [0.62, 1.16], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
            easing: Easing.bezier(0.16, 1, 0.3, 1),
            output: 'perceptual-scale',
          }),
          right: -40 + seededUnit(seed, 3) * 180,
          top: (motif === 'pulse' ? 260 : 70) + seededUnit(seed, 4) * 100,
          rotate: motif === 'grid' ? '45deg' : '0deg',
        }}
      />
      <Interactive.Div
        name="Graphic eyebrow"
        style={{
          position: 'absolute',
          right: 190,
          top: 235,
          width: 500,
          color: accentColor,
          fontFamily: 'Arial, sans-serif',
          fontSize: 22,
          fontWeight: 800,
          letterSpacing: 7,
          textAlign: 'center',
          opacity: interpolate(frame, [8, Math.min(endFrame, 24)], [0, 0.94], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
          }),
        }}
      >
        {eyebrow}
      </Interactive.Div>
      {primary ? (
        <Interactive.Div
          name="Graphic primary text"
          style={{
            position: 'absolute',
            right: 120,
            top: 300,
            width: 650,
            color: 'white',
            fontFamily: 'Arial, sans-serif',
            fontSize: 62,
            fontWeight: 800,
            lineHeight: 1.05,
            textAlign: 'center',
            whiteSpace: 'pre-line',
            opacity: interpolate(frame, [8, Math.min(endFrame, 24)], [0, 0.94], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
            }),
          }}
        >
          {primary}
        </Interactive.Div>
      ) : null}
      {secondary ? (
        <Interactive.Div
          name="Graphic supporting text"
          style={{
            position: 'absolute',
            right: 165,
            top: 475,
            width: 560,
            color: '#d3d5d9',
            fontFamily: 'Arial, sans-serif',
            fontSize: 26,
            lineHeight: 1.25,
            textAlign: 'center',
            opacity: 0.78,
          }}
        >
          {secondary}
        </Interactive.Div>
      ) : null}
    </AbsoluteFill>
  );
};

export const BrollSegment: React.FC<SegmentProps> = ({
  placement,
  accentColor = '#e51d2a',
}) => {
  const frame = useCurrentFrame();
  const {durationInFrames, fps} = useVideoConfig();
  const asset = placement.asset;
  const source = asset?.src ? assetSource(asset.src) : null;
  const kind = inferSceneKind(placement);
  const title = placement.on_screen_text
    || placement.on_screen_text_bg
    || placement.beat_id
    || 'Product detail';
  const endFrame = Math.max(1, durationInFrames - 1);
  const fadeFrames = Math.min(12, Math.max(1, Math.floor(endFrame / 3)));
  const fadeOutStart = Math.max(fadeFrames, endFrame - fadeFrames);

  return (
    <AbsoluteFill name="B-roll segment" style={{backgroundColor: '#080808', overflow: 'hidden'}}>
      {source && asset?.kind === 'video' ? (
        <Video
          name={kind === 'source_cutaway' ? 'Project source cutaway' : 'Evidence video'}
          src={source}
          durationInFrames={durationInFrames}
          {...trimFrames(asset, fps)}
          volume={asset.include_audio
            ? Math.max(0, Math.min(1, asset.audio_volume ?? 1))
            : 0}
          style={mediaMotionStyle(placement, kind, frame, durationInFrames)}
        />
      ) : source ? (
        <Img
          name={kind === 'generated_illustration' ? 'AI-generated illustrative image' : 'Evidence image'}
          src={source}
          style={mediaMotionStyle(placement, kind, frame, durationInFrames)}
        />
      ) : (
        <GraphicScene placement={placement} accentColor={accentColor} />
      )}

      <AbsoluteFill style={{background: 'linear-gradient(90deg, rgba(0,0,0,.86), rgba(0,0,0,.08) 72%)'}} />
      <Interactive.Div
        name="Feature title"
        style={{
          position: 'absolute',
          left: 96,
          bottom: 150,
          width: 1100,
          color: 'white',
          fontFamily: 'Arial, sans-serif',
          fontSize: 84,
          fontWeight: 800,
          lineHeight: 1.02,
          letterSpacing: -2,
          opacity: interpolate(frame, [0, fadeFrames, fadeOutStart, endFrame], [0, 1, 1, 0], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
            easing: Easing.bezier(0.16, 1, 0.3, 1),
          }),
          translate: interpolate(frame, [0, Math.min(endFrame, 16)], ['-70px 0px', '0px 0px'], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
            easing: Easing.spring({damping: 200}),
          }),
        }}
      >
        {title}
      </Interactive.Div>
      <Interactive.Div
        name="Evidence disclosure"
        style={{
          position: 'absolute',
          left: 100,
          bottom: 96,
          color: '#d6d6d6',
          fontFamily: 'Arial, sans-serif',
          fontSize: 28,
          opacity: 0.9,
        }}
      >
        {disclosureFor(placement, kind)}
      </Interactive.Div>
    </AbsoluteFill>
  );
};
