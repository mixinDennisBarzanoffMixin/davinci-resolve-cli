import React from 'react';
import {Audio} from '@remotion/media';
import {AbsoluteFill, interpolate, Sequence, staticFile, useVideoConfig} from 'remotion';
import {AnimatedCaptions} from './AnimatedCaptions';
import {BrollSegment} from './BrollSegment';
import type {MusicTrack, ProductionProps} from './types';

const isLocalAssetPath = (src: string) =>
  Boolean(src)
  && !/^(?:https?:|data:|file:)/i.test(src)
  && !src.startsWith('/')
  && !src.split(/[\\/]+/).includes('..');

const MusicBed: React.FC<{
  music: MusicTrack;
  timelineDurationSeconds: number;
}> = ({music, timelineDurationSeconds}) => {
  const {fps} = useVideoConfig();
  if (music.license_review_status !== 'approved' || !isLocalAssetPath(music.src)) {
    return null;
  }
  const startSeconds = Math.max(0, music.start_seconds ?? 0);
  const endSeconds = Math.min(
    timelineDurationSeconds,
    Math.max(startSeconds, music.end_seconds ?? timelineDurationSeconds),
  );
  const durationInFrames = Math.max(1, Math.round((endSeconds - startSeconds) * fps));
  const fadeInFrames = Math.min(
    Math.floor(durationInFrames / 2),
    Math.max(0, Math.round((music.fade_in_seconds ?? 0) * fps)),
  );
  const fadeOutFrames = Math.min(
    Math.floor(durationInFrames / 2),
    Math.max(0, Math.round((music.fade_out_seconds ?? 0) * fps)),
  );
  const baseVolume = Math.max(0, Math.min(1, music.volume ?? 0.18));

  return (
    <Audio
      name="Licensed music bed"
      src={staticFile(music.src)}
      from={Math.round(startSeconds * fps)}
      durationInFrames={durationInFrames}
      volume={(localFrame) => {
        const fadeIn = fadeInFrames > 0
          ? interpolate(localFrame, [0, fadeInFrames], [0, 1], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
          })
          : 1;
        const fadeOut = fadeOutFrames > 0
          ? interpolate(
            localFrame,
            [durationInFrames - fadeOutFrames, durationInFrames],
            [1, 0],
            {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
          )
          : 1;
        return baseVolume * Math.min(fadeIn, fadeOut);
      }}
    />
  );
};

export const ProductionPreview: React.FC<ProductionProps> = ({
  captions,
  placements,
  music,
  timelineDurationSeconds,
}) => {
  const {fps, width, height} = useVideoConfig();
  return (
    <AbsoluteFill style={{backgroundColor: '#111'}}>
      {music ? <MusicBed music={music} timelineDurationSeconds={timelineDurationSeconds} /> : null}
      {placements.map((placement) => {
        const from = Math.round(placement.start_seconds * fps);
        const durationSeconds = placement.duration_seconds
          ?? placement.duration_sec
          ?? Math.max(0, (placement.end_seconds ?? placement.start_seconds) - placement.start_seconds);
        const durationInFrames = Math.max(1, Math.round(durationSeconds * fps));
        return (
          <Sequence
            key={placement.id}
            name={placement.beat_id || placement.id}
            from={from}
            durationInFrames={durationInFrames}
            premountFor={fps}
          >
            <BrollSegment placement={placement} durationInFrames={durationInFrames} fps={fps} width={width} height={height} />
          </Sequence>
        );
      })}
      <AnimatedCaptions captions={captions} />
    </AbsoluteFill>
  );
};
