from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class BaseStates(str, Enum):
    pass


@dataclass()
class State:
    name: str
    data: Optional[dict]


class AbstractStateManager(ABC):
    storage: Dict[str, State]

    @abstractmethod
    def get_state(self, sender: str) -> Optional[State]:
        pass

    @abstractmethod
    def set_state(self, sender: str, state_name: str) -> None:
        pass

    @abstractmethod
    def update_state(self, sender: str, state_name: str) -> None:
        pass

    @abstractmethod
    def delete_state(self, sender: str) -> None:
        pass

    @abstractmethod
    def get_state_data(self, sender: str) -> Optional[dict]:
        pass

    @abstractmethod
    def set_state_data(self, sender: str, state_data: dict) -> None:
        pass

    @abstractmethod
    def update_state_data(self, sender: str, state_data: dict) -> None:
        pass

    @abstractmethod
    def delete_state_data(self, sender: str) -> None:
        pass


class StateManager(AbstractStateManager):
    def __init__(self):
        self.storage = {}

    def get_state(self, sender: str) -> Optional[State]:
        return self.storage.get(sender)

    def set_state(self, sender: str, state_name: str) -> None:
        self.storage[sender] = State(state_name, None)

    def update_state(self, sender: str, state_name: str) -> None:
        state = self.get_state(sender)
        if state:
            state.name = state_name
        else:
            self.set_state(sender, state_name)

    def delete_state(self, sender: str) -> None:
        self.storage.pop(sender, None)

    def get_state_data(self, sender: str) -> Optional[dict]:
        state = self.get_state(sender)
        if state:
            return state.data

    def set_state_data(self, sender: str, state_data: dict) -> None:
        state = self.get_state(sender)
        if state:
            state.data = state_data

    def update_state_data(self, sender: str, state_data: dict) -> None:
        state = self.get_state(sender)
        if state:
            if not state.data:
                self.set_state_data(sender, state_data)
            else:
                for key, value in state_data.items():
                    state.data[key] = value

    def delete_state_data(self, sender: str) -> None:
        state = self.get_state(sender)
        if state:
            state.data = None


__all__ = ["BaseStates", "State", "AbstractStateManager", "StateManager"]
