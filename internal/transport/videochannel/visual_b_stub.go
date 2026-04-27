//go:build !b

package videochannel

func renderVisualFrameB(payload []byte, width, height, modulePx, colors int, recoveryLevel string) ([]byte, error) {
	return renderVisualFrame(payload, width, height, recoveryLevel)
}

func extractVisualPayloadB(frame []byte, width, height, modulePx, colors int) ([]byte, error) {
	return extractVisualPayload(frame, width, height)
}
