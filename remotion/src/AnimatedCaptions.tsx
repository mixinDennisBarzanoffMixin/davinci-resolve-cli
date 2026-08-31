import React, {useMemo} from 'react';
import {createTikTokStyleCaptions} from '@remotion/captions';
import type {Caption, TikTokPage} from '@remotion/captions';
import {
  AbsoluteFill,
  Easing,
  Interactive,
  Sequence,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

const CaptionPage: React.FC<{page: TikTokPage}> = ({page}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const absoluteTimeMs = page.startMs + (frame / fps) * 1000;
  return (
    <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: 106}}>
      <Interactive.Div
        name="Animated Bulgarian caption"
        style={{
          maxWidth: 1540,
          borderRadius: 28,
          backgroundColor: 'rgba(0,0,0,.76)',
          padding: '22px 38px 26px',
          color: 'white',
          fontFamily: 'Arial, sans-serif',
          fontSize: 64,
          fontWeight: 800,
          lineHeight: 1.12,
          textAlign: 'center',
          whiteSpace: 'pre-wrap',
          scale: interpolate(frame, [0, 8], [0.94, 1], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
            easing: Easing.spring({damping: 200}),
            output: 'perceptual-scale',
          }),
        }}
      >
        {page.tokens.map((token) => (
          <span
            key={`${token.fromMs}-${token.toMs}`}
            style={{color: token.fromMs <= absoluteTimeMs && token.toMs > absoluteTimeMs ? '#ff3344' : 'white'}}
          >
            {token.text}
          </span>
        ))}
      </Interactive.Div>
    </AbsoluteFill>
  );
};

export const AnimatedCaptions: React.FC<{captions: Caption[]}> = ({captions}) => {
  const {fps} = useVideoConfig();
  const {pages} = useMemo(() => createTikTokStyleCaptions({
    captions,
    combineTokensWithinMilliseconds: 1100,
  }), [captions]);
  return (
    <AbsoluteFill>
      {pages.map((page, index) => {
        const startFrame = Math.round((page.startMs / 1000) * fps);
        const lastToken = page.tokens[page.tokens.length - 1];
        const pageEndMs = Math.max(page.startMs + 1, lastToken?.toMs ?? page.startMs + 1);
        const endFrame = Math.max(startFrame + 1, Math.round((pageEndMs / 1000) * fps));
        return (
          <Sequence key={`${page.startMs}-${index}`} from={startFrame} durationInFrames={endFrame - startFrame}>
            <CaptionPage page={page} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
