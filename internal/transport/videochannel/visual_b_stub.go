//go:build !b

package videochannel

func renderVisualFrameB(payload []byte, width, height int) ([]byte, error) {
	return renderVisualFrame(payload, width, height)
}

func extractVisualPayloadB(frame []byte, width, height int) ([]byte, error) {
	return extractVisualPayload(frame, width, height)
}
