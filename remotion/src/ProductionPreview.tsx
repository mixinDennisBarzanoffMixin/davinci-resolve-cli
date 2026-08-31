import React from 'react';
import {AbsoluteFill, Sequence, useVideoConfig} from 'remotion';
import {AnimatedCaptions} from './AnimatedCaptions';
import {BrollSegment} from './BrollSegment';
import type {ProductionProps} from './types';

export const ProductionPreview: React.FC<ProductionProps> = ({captions, placements}) => {
  const {fps, width, height} = useVideoConfig();
  return (
    <AbsoluteFill style={{backgroundColor: '#111'}}>
      {placements.map((placement) => {
        const from = Math.round(placement.start_seconds * fps);
        const durationInFrames = Math.max(1, Math.round(placement.duration_seconds * fps));
        return (
          <Sequence key={placement.id} name={placement.beat_id || placement.id} from={from} durationInFrames={durationInFrames}>
            <BrollSegment placement={placement} durationInFrames={durationInFrames} fps={fps} width={width} height={height} />
          </Sequence>
        );
      })}
      <AnimatedCaptions captions={captions} />
    </AbsoluteFill>
  );
};
