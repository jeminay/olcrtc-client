package telemost

import "testing"

func TestIsConferenceEndMessage(t *testing.T) {
	tests := []map[string]interface{}{
		{"conferenceEnded": map[string]interface{}{}},
		{"conference": map[string]interface{}{"state": "closed"}},
		{"conferenceState": map[string]interface{}{"state": "TERMINATED"}},
	}

	for _, tt := range tests {
		if !isConferenceEndMessage(tt) {
			t.Fatalf("expected end message for %#v", tt)
		}
	}
}

func TestIsConferenceEndMessageIgnoresActiveState(t *testing.T) {
	msg := map[string]interface{}{
		"conference": map[string]interface{}{"state": "active"},
	}
	if isConferenceEndMessage(msg) {
		t.Fatal("active conference state must not be treated as ended")
	}
}
