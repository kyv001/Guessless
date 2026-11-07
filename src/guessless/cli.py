from guessless.core import *  # pyright: ignore[reportWildcardImportFromLibrary]


class TUI(Minesweeper):
    def __repr__(self) -> str:
        s = "   " + "".join(f"{x:^3}" for x in range(self.size.w)) + "\n"
        for y in range(self.size.h):
            s += f"{y:2} "
            for x in range(self.size.w):
                cell = self.get_at(Position(x, y))
                if cell.revealed:
                    if cell.number == 0:
                        s += "   "
                    else:
                        s += f"[{cell.number}]"
                elif cell.flagged:
                    s += "[F]"
                else:
                    s += "[#]"
            s += "\n"
        return s

    def __repr_inspection__(self) -> str:
        s = "   " + "".join(f"{x:^3}" for x in range(self.size.w)) + "\n"
        for y in range(self.size.h):
            s += f"{y:2} "
            for x in range(self.size.w):
                pos = Position(x, y)
                if self.last_solve_result is None:
                    s += "   "
                    continue
                if pos in self.last_solve_result.must_be_mine:
                    s += "[M]"
                elif pos in self.last_solve_result.must_be_safe:
                    s += "[S]"
                elif pos in self.last_solve_result.cannot_deter:
                    s += "[?]"
                else:
                    s += "   "
            s += "\n"
        return s

    def main(self) -> None:
        print(f"{self.size.w}x{self.size.h}@{self.n_mines}")
        while True:
            print(self)
            if self.state == GameState.LOST:
                print("LOST")
                print(self.__repr_inspection__())
                break
            if self.state == GameState.WON:
                print("WON")
                break
            instruct = input(">")
            match instruct.split():
                case ["f", x, y, *_]: # flag
                    self.flag_at(Position(int(x), int(y)))
                case ["r", x, y, *_]: # reveal
                    self.reveal_at(Position(int(x), int(y)), skip_solve=True)
                case ["i", *_]: # inspect
                    self.solve()
                    print(self.__repr_inspection__())

if __name__ == "__main__":
    TUI(Size(10, 10), 10).main()
