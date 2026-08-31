import React from 'react';
import {Audio, Video} from '@remotion/media';
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
import type {SegmentProps} from './types';

const assetSource = (src: string) =>
  src.startsWith('http://') || src.startsWith('https://') ? src : staticFile(src.replace(/^\//, ''));

export const BrollSegment: React.FC<SegmentProps> = ({
  placement,
  accentColor = '#e51d2a',
}) => {
  const frame = useCurrentFrame();
  const {durationInFrames, fps} = useVideoConfig();
  const source = placement.asset?.src ? assetSource(placement.asset.src) : null;
  const title = placement.on_screen_text || placement.beat_id || 'Feature';
  const visualCode: Record<string, string> = {
    'lpg-powertrain': '3.5 LPI\nV6 → 8AT',
    'comfort-features': '♨ SEAT\n↕ AIR',
    'driver-assistance': 'LANE · P',
    'aftercare-service': 'SERVICE\nPARTS',
    'warranty-claim': '1–3\nYEARS',
    'promotion-value': '−1 000 €',
    'price-open': '23 014 €',
    'closing-verification': 'VIN ✓',
  };

  return (
    <AbsoluteFill name="B-roll segment" style={{backgroundColor: '#080808', overflow: 'hidden'}}>
      {source && placement.asset?.kind === 'video' ? (
        <Video
          name="Evidence video"
          src={source}
          durationInFrames={durationInFrames}
          style={{width: '100%', height: '100%', objectFit: 'cover'}}
        />
      ) : source ? (
        <Img
          name="Evidence image"
          src={source}
          style={{
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            scale: interpolate(frame, [0, durationInFrames - 1], [1.02, 1.09], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
              easing: Easing.bezier(0.16, 1, 0.3, 1),
              output: 'perceptual-scale',
            }),
          }}
        />
      ) : (
        <AbsoluteFill
          name="Truthful motion graphic fallback"
          style={{
            background: 'radial-gradient(circle at 68% 38%, #382227 0%, #101113 42%, #060607 100%)',
          }}
        >
          <Interactive.Div
            name="Feature rings"
            style={{
              position: 'absolute',
              width: 680,
              height: 680,
              borderRadius: 999,
              border: `10px solid ${accentColor}`,
              opacity: interpolate(frame, [0, fps, durationInFrames - fps, durationInFrames - 1], [0, 0.42, 0.42, 0], {
                extrapolateLeft: 'clamp',
                extrapolateRight: 'clamp',
                easing: Easing.bezier(0.16, 1, 0.3, 1),
              }),
              scale: interpolate(frame, [0, durationInFrames - 1], [0.58, 1.18], {
                extrapolateLeft: 'clamp',
                extrapolateRight: 'clamp',
                easing: Easing.bezier(0.16, 1, 0.3, 1),
                output: 'perceptual-scale',
              }),
              left: 1100,
              top: 100,
            }}
          />
          <Interactive.Div
            name="Evidence-safe diagram label"
            style={{
              position: 'absolute',
              left: 1180,
              top: 300,
              width: 520,
              color: 'white',
              fontFamily: 'Arial, sans-serif',
              fontSize: 58,
              fontWeight: 800,
              lineHeight: 1.05,
              textAlign: 'center',
              whiteSpace: 'pre-line',
              opacity: interpolate(frame, [8, 22], [0, 0.92], {
                extrapolateLeft: 'clamp',
                extrapolateRight: 'clamp',
              }),
            }}
          >
            {visualCode[placement.beat_id || ''] || 'K8'}
          </Interactive.Div>
        </AbsoluteFill>
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
          opacity: interpolate(frame, [0, 12, durationInFrames - 12, durationInFrames - 1], [0, 1, 1, 0], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
            easing: Easing.bezier(0.16, 1, 0.3, 1),
          }),
          translate: interpolate(frame, [0, 16], ['-70px 0px', '0px 0px'], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
            easing: Easing.spring({damping: 200}),
          }),
        }}
      >
        {title}
      </Interactive.Div>
      <Interactive.Div
        name="Evidence label"
        style={{
          position: 'absolute',
          left: 100,
          bottom: 96,
          color: '#d6d6d6',
          fontFamily: 'Arial, sans-serif',
          fontSize: 28,
          opacity: 0.86,
        }}
      >
        {placement.asset?.exact_item ? 'Точният автомобил · източник: обявата' : 'Илюстративна графика · провери оборудването'}
      </Interactive.Div>
    </AbsoluteFill>
  );
};
