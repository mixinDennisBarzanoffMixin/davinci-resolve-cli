import React from 'react';
import {
  AbsoluteFill,
  Composition,
  Easing,
  Interactive,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import {
  ProductionCardIllustration,
  type ProductionCardKind,
} from './ProductionCardIllustrations';

export type ProductionCardFact = {
  label: string;
  value: string;
};

export type ProductionCardProps = {
  kind: ProductionCardKind;
  eyebrow: string;
  title: string;
  subtitle: string;
  facts: ProductionCardFact[];
  accentColor?: string;
  footer?: string;
  durationInFrames: number;
  fps: number;
  width: number;
  height: number;
};

const FPS = 60;
const DURATION_IN_FRAMES = 300;
const WIDTH = 1080;
const HEIGHT = 1920;
const ACCENT = '#eb1c2a';
const portraitDefaults = {
  durationInFrames: DURATION_IN_FRAMES,
  fps: FPS,
  width: WIDTH,
  height: HEIGHT,
};

const presets: Array<{id: string; props: ProductionCardProps}> = [
  {
    id: 'Card-Powertrain',
    props: {
      ...portraitDefaults,
      kind: 'powertrain',
      eyebrow: 'ЗАДВИЖВАНЕ',
      title: 'Мощност с ясни параметри',
      subtitle: 'Поднесете проверените данни кратко — без да симулирате точния автомобил.',
      facts: [
        {label: 'ДВИГАТЕЛ', value: 'по спецификация'},
        {label: 'ТРАНСМИСИЯ', value: 'проверена версия'},
        {label: 'ГОРИВО', value: 'ясно обозначено'},
      ],
    },
  },
  {
    id: 'Card-DoorAccess',
    props: {
      ...portraitDefaults,
      kind: 'door-access',
      eyebrow: 'ДОСТЪП',
      title: 'Врати и удобен достъп',
      subtitle: 'Покажете функцията като схема, когато няма одобрен кадър от точния автомобил.',
      facts: [
        {label: 'ОТВАРЯНЕ', value: 'широк достъп'},
        {label: 'ЗАТВАРЯНЕ', value: 'плавно движение'},
        {label: 'ПРАКТИЧНОСТ', value: 'за всеки ден'},
      ],
    },
  },
  {
    id: 'Card-Cabin',
    props: {
      ...portraitDefaults,
      kind: 'cabin',
      eyebrow: 'ИНТЕРИОР',
      title: 'Комфорт в купето',
      subtitle: 'Групирайте потвърдените екстри, вместо да показвате несъществуващ салон.',
      facts: [
        {label: 'СЕДАЛКИ', value: 'комфорт'},
        {label: 'КЛИМАТ', value: 'зони по данни'},
        {label: 'УПРАВЛЕНИЕ', value: 'лесен достъп'},
      ],
    },
  },
  {
    id: 'Card-ServiceParts',
    props: {
      ...portraitDefaults,
      kind: 'service-parts',
      eyebrow: 'ПОДДРЪЖКА',
      title: 'Сервиз и части',
      subtitle: 'Практична информация за обслужването, подкрепена с проверим източник.',
      facts: [
        {label: 'КОНСУМАТИВИ', value: 'проверени'},
        {label: 'ЧАСТИ', value: 'по каталог'},
        {label: 'СЕРВИЗ', value: 'след оглед'},
      ],
    },
  },
  {
    id: 'Card-Warranty',
    props: {
      ...portraitDefaults,
      kind: 'warranty',
      eyebrow: 'СИГУРНОСТ',
      title: 'Гаранция с ясни условия',
      subtitle: 'Срокът и покритието трябва да идват от одобрените условия за конкретната оферта.',
      facts: [
        {label: 'СРОК', value: 'по договор'},
        {label: 'ПОКРИТИЕ', value: 'описано ясно'},
        {label: 'УСЛОВИЯ', value: 'без дребен шрифт'},
      ],
    },
  },
  {
    id: 'Card-PriceReduction',
    props: {
      ...portraitDefaults,
      kind: 'price-reduction',
      eyebrow: 'ЦЕНА',
      title: 'По-добра оферта',
      subtitle: 'Използвайте само актуалните стойности от одобрения листинг или ценова таблица.',
      facts: [
        {label: 'СТАРА ЦЕНА', value: 'проверена'},
        {label: 'НОВА ЦЕНА', value: 'актуална'},
        {label: 'РАЗЛИКА', value: 'изчислена'},
      ],
    },
  },
  {
    id: 'Card-Inventory',
    props: {
      ...portraitDefaults,
      kind: 'inventory',
      eyebrow: 'ИЗБОР',
      title: 'Повече автомобили на едно място',
      subtitle: 'Графиката представя избор, а не твърди конкретна наличност без актуална проверка.',
      facts: [
        {label: 'МОДЕЛИ', value: 'различни класове'},
        {label: 'ОФЕРТИ', value: 'на едно място'},
        {label: 'НАЛИЧНОСТ', value: 'проверете сега'},
      ],
    },
  },
  {
    id: 'Card-CTA',
    props: {
      ...portraitDefaults,
      kind: 'cta',
      eyebrow: 'СЛЕДВАЩА СТЪПКА',
      title: 'Вижте актуалната оферта',
      subtitle: 'Сравнете оборудването, историята и условията преди да вземете решение.',
      facts: [
        {label: 'ОНЛАЙН', value: 'carsbg11.com'},
        {label: 'КОНТАКТ', value: 'поискайте оглед'},
        {label: 'ИЗБОР', value: 'проверете наличност'},
      ],
      footer: 'CARSBG11 · КОРЕЯ → БЪЛГАРИЯ',
    },
  },
];

export const ProductionCard: React.FC<ProductionCardProps> = ({
  kind,
  eyebrow,
  title,
  subtitle,
  facts,
  accentColor = ACCENT,
  footer = 'CARSBG11 · ИЛЮСТРАТИВНА ГРАФИКА',
}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const endFrame = Math.max(1, durationInFrames - 1);
  const exitStart = Math.max(60, endFrame - 30);

  return (
    <AbsoluteFill
      name="CarsBG11 production card"
      style={{
        overflow: 'hidden',
        backgroundColor: '#070809',
        color: '#f8f8f8',
        fontFamily: 'Arial, Helvetica, sans-serif',
      }}
    >
      <AbsoluteFill
        style={{
          opacity: interpolate(frame, [0, 24], [0, 1], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
          }),
          background: 'radial-gradient(circle at 50% 52%, #2a1013 0%, #0d0e10 34%, #070809 68%)',
        }}
      />
      <Interactive.Div
        name="Brand rail"
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          width: interpolate(frame, [0, 56], [0, 1080], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
            easing: Easing.bezier(0.16, 1, 0.3, 1),
          }),
          height: 10,
          backgroundColor: accentColor,
        }}
      />
      <Interactive.Div
        name="Editorial index"
        style={{
          position: 'absolute',
          left: 72,
          top: 142,
          right: 72,
          display: 'flex',
          justifyContent: 'space-between',
          color: '#979aa0',
          fontSize: 17,
          fontWeight: 700,
          letterSpacing: 4,
          opacity: interpolate(frame, [16, 48, exitStart, endFrame], [0, 1, 1, 0], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
          }),
        }}
      >
        <span>{footer}</span>
        <span>ПРОВЕРЕНО СЪДЪРЖАНИЕ</span>
      </Interactive.Div>
      <div style={{position: 'absolute', left: 72, right: 72, top: 204, height: 1, background: '#34363b'}} />

      <Interactive.Div
        name="Card copy"
        style={{
          position: 'absolute',
          left: 72,
          top: 268,
          width: 936,
          opacity: interpolate(frame, [10, 48, exitStart, endFrame], [0, 1, 1, 0], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
            easing: Easing.bezier(0.16, 1, 0.3, 1),
          }),
          translate: interpolate(frame, [10, 56], ['-64px 0px', '0px 0px'], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
            easing: Easing.spring({damping: 180}),
          }),
        }}
      >
        <div style={{display: 'flex', alignItems: 'center', gap: 18, color: accentColor, fontSize: 24, fontWeight: 900, letterSpacing: 7}}>
          <span style={{display: 'block', width: 48, height: 5, backgroundColor: accentColor}} />
          {eyebrow}
        </div>
        <div style={{marginTop: 30, maxWidth: 936, fontSize: 88, fontWeight: 900, lineHeight: 1.01, letterSpacing: -2.8}}>
          {title}
        </div>
        <div style={{marginTop: 30, maxWidth: 900, color: '#b9bbc0', fontSize: 32, lineHeight: 1.38}}>
          {subtitle}
        </div>
      </Interactive.Div>

      <Interactive.Div
        name="Conceptual illustration"
        style={{
          position: 'absolute',
          left: 90,
          top: 745,
          width: 900,
          height: 650,
        }}
      >
        <ProductionCardIllustration kind={kind} accentColor={accentColor} />
      </Interactive.Div>

      <Interactive.Div
        name="Verified fact slots"
        style={{
          position: 'absolute',
          left: 72,
          right: 72,
          bottom: 238,
          display: 'grid',
          gridTemplateColumns: '1fr',
          gap: 12,
        }}
      >
        {facts.slice(0, 3).map((fact, index) => (
          <div
            key={`${fact.label}-${index}`}
            style={{
              minHeight: 82,
              padding: '18px 24px',
              border: '1px solid #393b40',
              borderTop: `5px solid ${index === 0 ? accentColor : '#5b5e64'}`,
              backgroundColor: '#111316',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 24,
              opacity: interpolate(frame, [40 + index * 12, 72 + index * 12, exitStart, endFrame], [0, 1, 1, 0], {
                extrapolateLeft: 'clamp',
                extrapolateRight: 'clamp',
                easing: Easing.bezier(0.16, 1, 0.3, 1),
              }),
              translate: interpolate(frame, [40 + index * 12, 76 + index * 12], ['0px 40px', '0px 0px'], {
                extrapolateLeft: 'clamp',
                extrapolateRight: 'clamp',
                easing: Easing.spring({damping: 190}),
              }),
            }}
          >
            <div style={{color: '#85888e', fontSize: 19, fontWeight: 800, letterSpacing: 3.5}}>{fact.label}</div>
            <div style={{color: '#f7f7f8', fontSize: 29, fontWeight: 800, textAlign: 'right'}}>{fact.value}</div>
          </div>
        ))}
      </Interactive.Div>

      <Interactive.Div
        name="Evidence-safe disclosure"
        style={{
          position: 'absolute',
          left: 72,
          right: 72,
          bottom: 142,
          color: '#74777d',
          fontSize: 17,
          textAlign: 'center',
          letterSpacing: 1.2,
          opacity: interpolate(frame, [56, 90, exitStart, endFrame], [0, 1, 1, 0], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
          }),
        }}
      >
        ИЛЮСТРАТИВНО · НЕ Е ТОЧНИЯТ АВТОМОБИЛ · ДАННИТЕ ПОДЛЕЖАТ НА ПРОВЕРКА
      </Interactive.Div>
    </AbsoluteFill>
  );
};

export const ProductionCardCompositions: React.FC = () => (
  <>
    {presets.map(({id, props}) => (
      <Composition
        key={id}
        id={id}
        component={ProductionCard}
        durationInFrames={DURATION_IN_FRAMES}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        calculateMetadata={({props: metadataProps}) => ({
          durationInFrames: metadataProps.durationInFrames,
          fps: metadataProps.fps,
          width: metadataProps.width,
          height: metadataProps.height,
        })}
        defaultProps={props}
      />
    ))}
  </>
);

export const PRODUCTION_CARD_PRESETS = presets;
