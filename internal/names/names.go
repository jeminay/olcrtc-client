package names

import (
	"bufio"
	_ "embed"
	"math/rand/v2"
	"os"
	"strings"
)

//go:embed data/names
var embeddedNames string

//go:embed data/surnames
var embeddedSurnames string

var (
	firstNames []string
	lastNames  []string
)

var defaultFirstNames = []string{
	"Александр", "Дмитрий", "Максим", "Сергей", "Андрей", "Алексей", "Артём", "Илья", "Кирилл", "Михаил",
	"Никита", "Матвей", "Роман", "Егор", "Арсений", "Иван", "Денис", "Евгений", "Даниил", "Тимофей",
	"Владислав", "Игорь", "Владимир", "Павел", "Руслан", "Марк", "Константин", "Николай", "Олег", "Виктор",
}

var defaultLastNames = []string{
	"Иванов", "Смирнов", "Кузнецов", "Попов", "Васильев", "Петров", "Соколов", "Михайлов", "Новиков", "Фёдоров",
	"Морозов", "Волков", "Алексеев", "Лебедев", "Семёнов", "Егоров", "Павлов", "Козлов", "Степанов", "Николаев",
	"Орлов", "Андреев", "Макаров", "Никитин", "Захаров", "Зайцев", "Соловьёв", "Борисов", "Яковлев", "Григорьев",
}

func parseEmbedded(raw string) []string {
	var names []string
	for _, line := range strings.Split(raw, "\n") {
		line = strings.TrimSpace(line)
		if line != "" {
			names = append(names, line)
		}
	}
	return names
}

func loadNames(path string) ([]string, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()

	var names []string
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line != "" {
			names = append(names, line)
		}
	}

	return names, scanner.Err()
}

func init() {
	if names := parseEmbedded(embeddedNames); len(names) > 0 {
		firstNames = names
	} else {
		firstNames = defaultFirstNames
	}

	if names := parseEmbedded(embeddedSurnames); len(names) > 0 {
		lastNames = names
	} else {
		lastNames = defaultLastNames
	}
}

func LoadNameFiles(firstPath, lastPath string) error {
	if names, err := loadNames(firstPath); err == nil && len(names) > 0 {
		firstNames = names
	}

	if names, err := loadNames(lastPath); err == nil && len(names) > 0 {
		lastNames = names
	}

	return nil
}

func Generate() string {
	if len(firstNames) == 0 {
		firstNames = defaultFirstNames
	}
	if len(lastNames) == 0 {
		lastNames = defaultLastNames
	}

	first := firstNames[rand.IntN(len(firstNames))]
	last := lastNames[rand.IntN(len(lastNames))]

	return first + " " + last
}
