import React from 'react';
import {Easing, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

export type ProductionCardKind =
  | 'powertrain'
  | 'door-access'
  | 'cabin'
  | 'service-parts'
  | 'warranty'
  | 'price-reduction'
  | 'inventory'
  | 'cta';

type IllustrationProps = {
  kind: ProductionCardKind;
  accentColor: string;
};

const line = {
  fill: 'none',
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  vectorEffect: 'non-scaling-stroke' as const,
};

const Powertrain = ({accentColor}: {accentColor: string}) => (
  <>
    <rect x="165" y="190" width="470" height="300" rx="52" stroke={accentColor} strokeWidth="16" {...line} />
    <path d="M220 190v-70h92v70M488 190v-70h92v70" stroke="#f4f4f5" strokeWidth="13" {...line} />
    <circle cx="300" cy="338" r="70" stroke="#f4f4f5" strokeWidth="14" {...line} />
    <circle cx="500" cy="338" r="70" stroke="#f4f4f5" strokeWidth="14" {...line} />
    <path d="M300 268v140M430 338h-60M500 268v140" stroke={accentColor} strokeWidth="12" {...line} />
    <path d="M635 285h74l44 53-44 53h-74" stroke="#f4f4f5" strokeWidth="15" {...line} />
    <path d="M142 268H82M142 338H56M142 408H82" stroke={accentColor} strokeWidth="12" {...line} />
  </>
);

const DoorAccess = ({accentColor}: {accentColor: string}) => (
  <>
    <path d="M178 510V190q0-55 55-55h286q55 0 55 55v320" stroke="#f4f4f5" strokeWidth="16" {...line} />
    <path d="M232 510V224q0-32 32-32h212q32 0 32 32v286" stroke={accentColor} strokeWidth="14" {...line} />
    <circle cx="454" cy="357" r="14" fill={accentColor} />
    <path d="M640 248h118M711 191l57 57-57 57" stroke="#f4f4f5" strokeWidth="15" {...line} />
    <path d="M640 430h118M697 373l-57 57 57 57" stroke={accentColor} strokeWidth="15" {...line} />
    <path d="M135 510h485" stroke="#f4f4f5" strokeWidth="16" {...line} />
  </>
);

const Cabin = ({accentColor}: {accentColor: string}) => (
  <>
    <path d="M173 484v-96q0-48 48-48h116q48 0 48 48v96M455 484v-96q0-48 48-48h116q48 0 48 48v96" stroke="#f4f4f5" strokeWidth="16" {...line} />
    <path d="M215 340v-98q0-36 36-36h56q36 0 36 36v98M497 340v-98q0-36 36-36h56q36 0 36 36v98" stroke={accentColor} strokeWidth="14" {...line} />
    <path d="M150 484h540" stroke="#f4f4f5" strokeWidth="16" {...line} />
    <path d="M404 170v258" stroke={accentColor} strokeWidth="10" strokeDasharray="18 22" {...line} />
    <path d="M282 274h-34M564 274h-34" stroke="#f4f4f5" strokeWidth="12" {...line} />
  </>
);

const ServiceParts = ({accentColor}: {accentColor: string}) => (
  <>
    <path d="M187 460l190-190M311 187q58-39 112 15l-66 66 51 51 66-66q54 54 15 112-37 55-105 41L239 551l-82-82 145-145q-14-68 41-105" stroke="#f4f4f5" strokeWidth="17" {...line} />
    <rect x="510" y="210" width="218" height="170" rx="24" stroke={accentColor} strokeWidth="15" {...line} />
    <path d="M510 278h218M619 210v170" stroke={accentColor} strokeWidth="11" {...line} />
    <path d="M546 455h146M546 507h108" stroke="#f4f4f5" strokeWidth="14" {...line} />
  </>
);

const Warranty = ({accentColor}: {accentColor: string}) => (
  <>
    <path d="M400 116q122 72 246 78v153q0 151-246 257-246-106-246-257V194q124-6 246-78Z" stroke={accentColor} strokeWidth="18" {...line} />
    <path d="M275 352l78 78 176-190" stroke="#f4f4f5" strokeWidth="24" {...line} />
    <path d="M210 189q85-12 190-73M590 189q-85-12-190-73" stroke="#f4f4f5" strokeWidth="9" opacity="0.42" {...line} />
  </>
);

const PriceReduction = ({accentColor}: {accentColor: string}) => (
  <>
    <path d="M174 180h474q54 0 54 54v262q0 54-54 54H174q-54 0-54-54V234q0-54 54-54Z" stroke="#f4f4f5" strokeWidth="16" {...line} />
    <path d="M212 297h390" stroke="#777b82" strokeWidth="18" {...line} />
    <path d="M175 430h332" stroke={accentColor} strokeWidth="26" {...line} />
    <path d="M568 357v138M513 440l55 55 55-55" stroke={accentColor} strokeWidth="18" {...line} />
    <circle cx="656" cy="219" r="24" fill={accentColor} />
  </>
);

const Inventory = ({accentColor}: {accentColor: string}) => (
  <>
    {[0, 1, 2].map((column) => [0, 1].map((row) => {
      const x = 112 + column * 232;
      const y = 142 + row * 220;
      const active = column === 1 && row === 0;
      return (
        <g key={`${column}-${row}`}>
          <rect x={x} y={y} width="184" height="162" rx="25" stroke={active ? accentColor : '#f4f4f5'} strokeWidth={active ? 16 : 11} {...line} />
          <path d={`M${x + 37} ${y + 102}h110l-18-39H${x + 58}l-21 39Zm14 0v27m82-27v27`} stroke={active ? '#f4f4f5' : '#a2a4aa'} strokeWidth="9" {...line} />
        </g>
      );
    }))}
  </>
);

const Cta = ({accentColor}: {accentColor: string}) => (
  <>
    <rect x="108" y="150" width="584" height="372" rx="32" stroke="#f4f4f5" strokeWidth="16" {...line} />
    <path d="M108 230h584" stroke={accentColor} strokeWidth="14" {...line} />
    <circle cx="158" cy="191" r="10" fill={accentColor} />
    <circle cx="192" cy="191" r="10" fill="#f4f4f5" opacity="0.45" />
    <path d="M189 319h424M189 382h302" stroke="#f4f4f5" strokeWidth="16" {...line} />
    <rect x="189" y="438" width="236" height="52" rx="15" fill={accentColor} />
    <path d="M589 414l93 54-51 16-18 49-24-119Z" fill="#f4f4f5" stroke="#08090b" strokeWidth="8" strokeLinejoin="round" />
  </>
);

const artwork = {
  powertrain: Powertrain,
  'door-access': DoorAccess,
  cabin: Cabin,
  'service-parts': ServiceParts,
  warranty: Warranty,
  'price-reduction': PriceReduction,
  inventory: Inventory,
  cta: Cta,
} satisfies Record<ProductionCardKind, React.FC<{accentColor: string}>>;

export const ProductionCardIllustration: React.FC<IllustrationProps> = ({kind, accentColor}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const Artwork = artwork[kind];
  const lastFrame = Math.max(1, durationInFrames - 1);

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        opacity: interpolate(frame, [12, 48, lastFrame - 30, lastFrame], [0, 1, 1, 0], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: Easing.bezier(0.16, 1, 0.3, 1),
        }),
        scale: interpolate(frame, [8, 60], [0.84, 1], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: Easing.spring({damping: 180}),
          output: 'perceptual-scale',
        }),
        rotate: interpolate(frame, [8, 60], ['-2deg', '0deg'], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
          easing: Easing.bezier(0.16, 1, 0.3, 1),
        }),
      }}
    >
      <svg viewBox="0 0 800 680" width="100%" height="100%" role="img" aria-label={`${kind} conceptual illustration`}>
        <Artwork accentColor={accentColor} />
      </svg>
    </div>
  );
};
