package names

import "testing"

func TestGenerateFallsBackWhenListsAreEmpty(t *testing.T) {
	oldFirst := firstNames
	oldLast := lastNames
	defer func() {
		firstNames = oldFirst
		lastNames = oldLast
	}()

	firstNames = nil
	lastNames = nil

	if got := Generate(); got == "" {
		t.Fatal("Generate returned an empty display name")
	}
}
