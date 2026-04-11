package main

import "fmt"

var currentProgram *Program

func log(msg string, args ...interface{}) {
	var formattedMsg string
	if len(args) > 0 {
		formattedMsg = fmt.Sprintf(msg, args...)
		fmt.Println(formattedMsg)
	} else {
		formattedMsg = msg
		fmt.Println(msg)
	}

	if currentProgram != nil && currentProgram.LogsChannel != nil {
		select {
		case currentProgram.LogsChannel <- formattedMsg:
		default:
		}
	}
}
