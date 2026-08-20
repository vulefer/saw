from dataclasses import dataclass

'''TODO: consider replacing the stock and material id with an int type'''

@dataclass(frozen=True, slots=True)
class Stock:
    stock_id: str
    material_id: str
    length: int
    quantity: int

@dataclass(frozen=True, slots=True)
class Demand:
    demand_id: str
    material_id: str
    length: int
    quantity: int

@dataclass(frozen=True, slots=True)
class Problem:

    #tuple[x,...] = zero-or-more x instances
    stocks: tuple[Stock, ...]
    demands: tuple[Demand, ...]

    def __post_init__(self) -> None:
        #'frozen' prevents changing the attribute value, but does not prevent
        # adding another stock into the tuple, this creates a copy of the tuple
        # in order to not reference the original tuple
        object.__setattr__(self, "stocks", tuple(self.stocks))
        object.__setattr__(self, "demands", tuple(self.demands))
