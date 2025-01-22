import random
import os
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from collections import deque

steps = 0
device = torch.device("cuda" if torch.cuda.is_available() else "cpu"); print(f"Using: {device}")

"""
# hyperparameters
AI_VERSION_NAME = "Buck_NLSCDDDQN_v0.3.8"
QUANT = 3
GAMMA = 0.999                       # discount factor    
#EPSILON = 0.1                         # exploration rate
#EPDECAY = 0.99999                 # exploration decay rate
#EPMIN = 0.02                        # minimum exploration rate
LR = 0.0005                         # learning rate
BATCH_SIZE = 128                     # how many samples to take from memory
MEMORY_SIZE = 100_000               # how many steps to store in memory
UPDATE_STEPS = 300                  # update target network every n steps
STEPS = 1_000_000                   # total steps to train
EPISODES = 650_536                  # suggest setting this to some very high value, may remove later
EVAL_RATIO = 10                    # evaluate every n episodes
EVAL_EPSIODES = 2                   # evaluate n episodes


class NoisyLinear(nn.Module):
    def __init__(self, in_features, out_features, *,std_init=0.4):
        super(NoisyLinear, self).__init__()

        self.weight_mu = nn.Parameter(torch.empty(out_features, in_features, device=device))
        self.weight_sigma = nn.Parameter(torch.empty(out_features, in_features, device=device))
        self.bias_mu = nn.Parameter(torch.empty(out_features, device=device))
        self.bias_sigma = nn.Parameter(torch.empty(out_features, device=device))
        
        self.register_buffer("weight_epsilon", torch.empty(out_features, in_features, device=device))
        self.register_buffer("bias_epsilon", torch.empty(out_features, device=device))
        
        self.std_init = std_init
        self.reset_parameters()

    def reset_parameters(self):
        bound = 1 / self.weight_mu.size(1)**0.5
        self.weight_mu.data.uniform_(-bound, bound)
        self.weight_sigma.data.fill_(self.std_init / self.weight_mu.size(1)**0.5)
        self.bias_mu.data.uniform_(-bound, bound)
        self.bias_sigma.data.fill_(self.std_init / self.bias_mu.size(1)**0.5)

    def forward(self, x):
        self.weight_epsilon.normal_()
        self.bias_epsilon.normal_()
        weight = self.weight_mu + self.weight_sigma * self.weight_epsilon
        bias = self.bias_mu + self.bias_sigma * self.bias_epsilon
        return torch.nn.functional.linear(x, weight, bias)
    

class NLSCDDDQN(nn.Module):
    def __init__(self, input_dim: int, output_dim: int, hidden_dims: list, *,
                 skip_connections: list = [], activation: nn.Module = nn.ReLU(),
                 use_noisy: bool = False, fully_noisy: bool = False, noise_std_init: float = 0.4):
        """ """
        Modular implementation of a Noisy Linear Skip-Connected Dueling Double Deep Q Network \n
        ------------- \n
        Parameters: \n
        Base DDDQN (inputs, outputs, hidden_dims) \n
        Optional NLSC (noisy, fully noisy, noise, skip connections) \n
        Misc (activation) \n
        ------------- \n
        """ """
        super(NLSCDDDQN, self).__init__()
        self.hidden_dims = hidden_dims
        self.activation = activation
        self.skip_connections = skip_connections
        self.use_noisy = True if (use_noisy or fully_noisy) else False

        self.hidden_layers = nn.ModuleList()
        prev_dim = input_dim
        for hidden_dim in hidden_dims:
            layer = NoisyLinear(prev_dim, hidden_dim, std_init=noise_std_init) if fully_noisy else nn.Linear(prev_dim, hidden_dim, device=device)
            self.hidden_layers.append(layer)
            prev_dim = hidden_dim
        
        self.value_fc = NoisyLinear(prev_dim, 1, std_init=noise_std_init) if use_noisy else nn.Linear(prev_dim, 1, device=device)
        self.advantage_fc = NoisyLinear(prev_dim, output_dim, std_init=noise_std_init) if use_noisy else nn.Linear(prev_dim, output_dim, device=device)
        
        self.skip_projections = nn.ModuleList()

        if skip_connections:
            for (from_layer, to_layer) in self.skip_connections:
                if from_layer == 0:
                    projection_layer = (
                        NoisyLinear(input_dim, hidden_dims[to_layer - 1])
                        if fully_noisy else nn.Linear(input_dim, hidden_dims[to_layer - 1], device=device)
                    )
                    self.skip_projections.append(projection_layer)
                else:
                    self.skip_projections.append(None)

    def forward(self, x):
        outputs = [x]
        for i, layer in enumerate(self.hidden_layers):
            x = self.activation(layer(x))
            if self.skip_connections:
                for (from_layer, to_layer) in self.skip_connections:
                    if to_layer == i + 1:
                        if from_layer == 0:
                            projected_input = self.skip_projections[0](outputs[from_layer])
                            x += projected_input
                        elif outputs[from_layer].shape[1] == x.shape[1]:
                            x += outputs[from_layer]
                        else:
                            raise ValueError(
                                f"Shape mismatch: cannot add output from layer {from_layer} with shape {outputs[from_layer].shape} "
                                f"to current layer with shape {x.shape}"
                            )
                outputs.append(x)

        value = self.value_fc(x).expand(x.size(0), self.advantage_fc.out_features)
        advantage = self.advantage_fc(x) - self.advantage_fc(x).mean(dim=1, keepdim=True)
        q_values = value + advantage
        return q_values


class DuelingDDQN(nn.Module):
    def __init__(self, input_dim: int, output_dim: int, hidden_dims: list, *,skip_connections: list = [], activation: nn.Module = nn.ReLU()):
        super(DuelingDDQN, self).__init__()
        self.hidden_dims = hidden_dims
        self.activation = activation
        self.skip_connections = skip_connections

        self.hidden_layers = nn.ModuleList()
        prev_dim = input_dim
        for hidden_dim in hidden_dims:
            self.hidden_layers.append(nn.Linear(prev_dim, hidden_dim, device=device))
            prev_dim = hidden_dim
        
        self.value_fc = nn.Linear(prev_dim, 1, device=device)
        self.advantage_fc = nn.Linear(prev_dim, output_dim, device=device)
        
        self.skip_projections = nn.ModuleList()

        if skip_connections:
            for (from_layer, to_layer) in self.skip_connections:
                if from_layer == 0: self.skip_projections.append(nn.Linear(input_dim, hidden_dims[to_layer - 1], device=device))
                else: self.skip_projections.append(None)

    def forward(self, x):
        outputs = [x]
        for i, layer in enumerate(self.hidden_layers):
            x = self.activation(layer(x))
            if self.skip_connections:
                for (from_layer, to_layer) in self.skip_connections:
                    if to_layer == i+1:
                        if from_layer == 0:
                            projected_input = self.skip_projections[0](outputs[from_layer])
                            x += projected_input
                        elif outputs[from_layer].shape[1] == x.shape[1]: x += outputs[from_layer]
                        else: raise ValueError(f"Shape mismatch: cannot add output from layer {from_layer} with shape {outputs[from_layer].shape} to current layer with shape {x.shape}")
                    
                outputs.append(x)

        value = self.value_fc(x).expand(x.size(0), self.advantage_fc.out_features)
        advantage = self.advantage_fc(x) - self.advantage_fc(x).mean(dim=1, keepdim=True)
        q_values = value + advantage
        return q_values


class DQNAgent:
    def __init__(self, inputs, outputs):
        self.inputs = inputs
        self.outputs = outputs
        self.memory_size = 100_000
        self.batch_size = 128
        #self.epsilon = EPSILON
        self.lr = 0.0005

        self.memory = deque(maxlen=self.memory_size)
        self.model = NLSCDDDQN(inputs, outputs, [128, 128]).to(device)
        self.target_model = NLSCDDDQN(inputs, outputs, [128, 128]).to(device)
        self.optimizer = optim.Adam(self.model.parameters(), lr=self.lr)
        self.loss_fn = nn.MSELoss().to(device)
        self.updateTargetNetwork()

    def updateTargetNetwork(self): self.target_model.load_state_dict(self.model.state_dict())

    def act(self, state):
        if np.random.rand() <= self.epsilon:
            return random.randrange(self.outputs)
        state = torch.FloatTensor(state).unsqueeze(0).to(device)
        with torch.no_grad():
            q_values = self.model(state)
        return torch.argmax(q_values).item()

    def remember(self, state, action, reward, next_state, done):
        experience = (state, action, reward, next_state, done)
        self.memory.append(experience)

    def replay(self):
        if len(self.memory) < self.batch_size:
            return  # Not enough data to sample a batch

        batch = random.sample(self.memory, self.batch_size)
        states, actions, rewards, next_states, dones = zip(*batch)

        states, next_states = np.array(states), np.array(next_states)
        states = torch.FloatTensor(states).to(device)
        actions = torch.LongTensor(actions).unsqueeze(1).to(device)
        rewards = torch.FloatTensor(rewards).to(device)
        next_states = torch.FloatTensor(next_states).to(device)
        dones = torch.FloatTensor(dones).to(device)

        q_values = self.model(states).gather(1, actions).squeeze()
        with torch.no_grad():
            max_next_q_values = self.target_model(next_states).max(1)[0]
            target_q_values = rewards + (1 - dones) * GAMMA * max_next_q_values

        loss = self.loss_fn(q_values, target_q_values)
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()


def saveModel(agent, filename=f"{AI_VERSION_NAME}_steps{steps}.pth"):
    if not os.path.exists("models"):
        os.makedirs("models")
        
    model_path = os.path.join("models", filename)
    torch.save({
        'model_state_dict': agent.model.state_dict(),
        'optimizer_state_dict': agent.optimizer.state_dict(),
        'steps': steps,
    }, model_path)
    print(f"Model saved to {model_path}")

def loadModel(agent, filename=f"{AI_VERSION_NAME}_steps{steps}.pth"):
    if not os.path.exists("models"):
        os.makedirs("models")
        
    model_path = os.path.join("models", filename)
    if os.path.exists(model_path):
        global steps
        checkpoint = torch.load(model_path)
        agent.model.load_state_dict(checkpoint['model_state_dict'])
        agent.target_model.load_state_dict(checkpoint['model_state_dict'])
        agent.optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
        steps = checkpoint['steps']
        print(f"Model loaded from {model_path}")
    else:
        print(f"Model not found in {model_path}")

def resetGame():
    pass

def printGame():
    pass

# Game loop
def playGame(agent, train=True):

    resetGame()

    def getState():
        flattened_state = np.array( dtype=np.float32)
        print(flattened_state)
        return flattened_state

    state = getState()


    running = True
    while running:
        pass
        


agent = DQNAgent((1+1+1+(8+8)+(8+8)+1+1+1), (6+2)); lastSteps = 0
#inputs: [(lives/4), (blanks/4), (round/3), [for (dogitem/6)+mask in dogitems], [for (dealeritem/6)+mask in dealeritems], (doghp/4), (dealer hp/4), (current shell/8)]
#outputs: [item actions, shoot who = end token]
# 1: use beer etc. 0: shoot ai(self), 7 shoot dealer(opp)
for e in range(EPISODES):
    if (e+1) % EVAL_RATIO == 0:
        _steps = steps
        for ep in range(EVAL_EPSIODES):
            playGame(agent, train=False)
        print(f"{(steps-lastSteps)//(EVAL_EPSIODES)}")
        steps = _steps
    else: playGame(agent)

    lastSteps = steps

    if steps >= STEPS: saveModel(agent); break
"""



MAX_SHELLS = 8
running = True
DEBUG =  True

class Game():
    def __init__(self):
        """Initializes the game state and shotgun."""
        self.max_shells = MAX_SHELLS
        self.live_shells, self.blank_shells, self.shells, self.shell, self.current_round_num = 0
        self.round = 0
        self.AI_items, self.DEALER_items = [] # 0:nothing, 1:beer 2:glass 3:smoke 4:inverter 5:cuffs 6:saw
        self.AI_can_play, self.DEALER_can_play = True
        self.AI_hp, self.DEALER_hp = 4
        self.invert_odds, self.is_sawed = False

        self.resetShells()
        print("Game initialized!")
    #####################################################################
    def resetShells(self):
        """Adds a random number of live and blank shells to the shotgun."""
        self.live_shells, self.blank_shells = random.randint(1, MAX_SHELLS//2), random.randint(1, MAX_SHELLS//2)
        self.shells = self.totalShells()
        self.current_round_num = 0
    #####################################################################
    def totalShells(self): return self.live_shells + self.blank_shells
    #####################################################################
    def determineShell(self):
        """Determines the type of shell in the current chamber, returns 1 for live and 0.5 for blank."""
        return 1 if random.random() <= (self.live_shells/self.shells) else 0.5
    #####################################################################
    def riggedDetermine(self, live: bool):
        """Determines the shell to be the chosen shell"""
        return 1 if live else 0.5
    #####################################################################
    def removeShell(self, live: bool):
        if live: self.live_shells -= 1
        else: self.blank_shells -= 1
    #####################################################################
    def resetGame(self):
        """Resets the game state, initializes the shotgun, and loads bullets."""
        self.resetShells()
        self.AI_items = []
        self.DEALER_items = []
        self.AI_hp = 4
        self.DEALER_hp = 4

        print("Game reset!")
    #####################################################################
    def printGame(self):
        """Prints the current game state for visualization."""
    #####################################################################
    def debugPrintGame(self):
        """Prints the current game state for debugging and visualization."""
        print(f"Current Round: {self.round}")
        print(f"AI HP: {self.AI_hp}, DEALER HP: {self.DEALER_hp}")
        print(f"Live Shells: {self.live_shells}, Blank Shells: {self.blank_shells}")
        print(f"Shells in Shotgun: {self.shells}")
        print(f"AI Items: {self.AI_items}")
        print(f"DEALER Items: {self.DEALER_items}")
        print(f"Current Round Number: {self.current_round_num}")
        print(f"Is SAWed?: {self.is_sawed}")
        print(f"Invert Odds?: {self.invert_odds}")
    #####################################################################
    def restockItems(self):
        """Restocks the round for the AI and DEALER."""
        for _ in range(self.round*2):
            if len(self.AI_items) < 8:
                self.AI_items.append(random.randint(1, 6)) 
                print("AI round: ", self.AI_items)

            if len(self.DEALER_items) < 8:
                self.DEALER_items.append(random.randint(1, 6)) 
                print("DEALER round: ", self.DEALER_items)
    #####################################################################
    def drinkBeer(self, player: str):
        """Player drinks beer, returns reward"""
        if self.totalShells == 1:
            raise Exception("are wii gunna have a problem?")
        def removeUnknownShell():
            if random.randint(0, 1) == 1 and self.live_shells > 0:
                self.live_shells -= 1
            elif self.blank_shells > 0:
                self.blank_shells -= 1
            else:
                raise Exception("No more shells!, this error should never happen")

        if player == "AI":
            if 1 in self.AI_items:
                self.AI_items.remove(1)
                if self.shell == 0: removeUnknownShell()
                elif self.shell == 1: self.live_shells -= 1
                else: self.blank_shells -= 1
                return 0.5
            else: return -3
        else:
            if 1 in self.DEALER_items:
                self.DEALER_items.remove(1)
    #####################################################################        
    def breakGlass(self, player: str):
        """Player breaks glass, determining shell, returns reward"""
        if player == "AI":
            if 2 in self.AI_items:
                self.AI_items.remove(2)
                self.shell = self.determineShell()
                return 1
            else: return -3
        else:
            if 2 in self.DEALER_items:
                self.DEALER_items.remove(2)           
    #####################################################################
    def smoke(self, player: str):
        """Player smokes, regains 1 hp, returns reward"""
        if player == "AI":
            if 3 in self.AI_items:
                self.AI_items.remove(3)
                self.AI_hp = min(4, self.AI_hp+1)
                return 1
            else: return -3
        else:
            if 3 in self.DEALER_items:
                self.DEALER_items.remove(3)
                self.DEALER_hp = min(4, self.DEALER_hp+1)
    #####################################################################
    def inverter(self, player: str):
        """Player inverts current round, returns reward"""
        def invert():
            if self.shell == 0.5: self.shell = 1
            elif self.shell == 1: self.shell = 0.5
            else: self.invert_odds = True

        if player == "AI":
            if 4 in self.AI_items:
                self.AI_items.remove(4)
                if self.shell == 0.5: self.blank_shells-=1; self.live_shells+=1
                elif self.shell == 1: self.blank_shells+=1; self.live_shells-=1
                invert()
                return 0.3
            else: return -3
        else:
            if 4 in self.DEALER_items:
                self.DEALER_items.remove(4)
    #####################################################################
    def cuff(self, player: str):
        """Player cuffs opponent, skipping their turn, returns reward"""
        if player == "AI":
            if 5 in self.AI_items:
                self.AI_items.remove(5)
                self.DEALER_can_play = False
                return 1
            else: return -3
        else:
            if 5 in self.DEALER_items:
                self.DEALER_items.remove(5)
                self.AI_can_play = False
    #####################################################################
    def saw(self, player: str):
        """Player saws off shotgun, doubling damage, returns reward"""
        if player == "AI":
            if 6 in self.AI_items:
                self.AI_items.remove(6)
                self.is_sawed = True
                return 1 if self.shell != 0.5 else -2
            else: return -3
        else:
            if 5 in self.DEALER_items:
                self.DEALER_items.remove(5)
                self.is_sawed = True
    #####################################################################
    def AIshootAI(self):
        """Determines the outcome of the shot if not already known, and shoots AI, returns reward"""
        if self.shell == 0:
            self.shell = self.determineShell()
            if self.shell == 1:
                self.AI_hp -= 1 if self.is_sawed == False else 2
                return -3 if self.is_sawed == False else -6 
            else: return 0 if self.is_sawed == False else -2

        elif self.shell == 0.5: return 2 if self.is_sawed == False else -8
        
        elif self.shell == 1:
            if self.is_sawed == False: self.AI_hp -= 1; return -20
            else: self.is_sawed = False; self.AI_hp -= 2; return -40
    #####################################################################    
    def AIshootDEALER(self, shell):
        """Determines the outcome of the shot if not already known, and shoots DEALER, returns reward"""
        if shell == 0:
            shell = self.determineShell()
            if shell == 1:
                if self.is_sawed == False: self.DEALER_hp -= 1; return 3
                else: self.is_sawed = False; self.DEALER_hp -= 2; return 6
            else: return 0

        elif shell == 1:
            if self.is_sawed == False: self.DEALER_hp -= 1; return 4
            else: self.is_sawed = False; self.DEALER_hp -= 2; return 8
        
        elif shell == 0.5: 
            if self.is_sawed == False:
                return -20 
            else: self.is_sawed = False; return -32
    #####################################################################
    def DEALERshootDEALER(self):
        """Determines the outcome of the shot if not already known, and shoots DEALER"""
        if shell == 0.5:
            self.AI_can_play = False

        elif shell == 0: # shell is unknown
            shell = self.determineShell()
            if shell == 1: # shell is live
                self.DEALER_hp -= 1
    #####################################################################
    def DEALERshootAI(self):
        """Determines the outcome of the shot if not already known, and shoots AI"""
        if shell == 1: self.AI_hp -= 1 if self.is_sawed == False else 2

        elif shell == 0: # shell is unknown
            shell = self.determineShell()
            if shell == 1: # shell is live
                self.AI_hp -= 1 if self.is_sawed == False else 2
    #####################################################################
    def DEALERALGO(self):
        """The DEALER Algorithm used in place of a real dealer, it has to cheat, but it efficiently trains the AI"""
        shells = self.blank_shells and self.live_shells
        canSuperCheat = (random.random() < 0.1) and shells
        canCheat = (random.random() < 0.3) and shells and not canSuperCheat
        cantCheat = not canCheat and not canSuperCheat

        def DEALERSmoke():
            """the DEALER smokes as many times as possible, stopping if he is at max hp"""
            for i in range(self.DEALER_items.count(3)):
                if self.DEALER_hp == 4: break
                self.smoke("DEALER")

        def normalCheat():
            """The cheating DEALER Algorithm, it makes the round live, (uses magnifiying glass if it has one), smokes if it can, cuffs AI if it can, then it shoots the AI"""
            self.riggedDetermine(live=True)
            self.breakGlass("DEALER")
            DEALERSmoke()
            self.cuff("DEALER")
            self.DEALERshootAI()
        
        def superCheat():
            """The SUPER cheating DEALER Algorithm, it makes the round blank, (uses magnifiying glass if it has one), smokes if it can, then it shoots itself; 
             it makes the round live (uses magnifiying glass if it has one), saws the gun if it can, cuffs the AI if it can, then shoots the AI"""
            self.riggedDetermine(live=False)
            self.breakGlass("DEALER")
            DEALERSmoke()
            self.DEALERshootDEALER()

            self.riggedDetermine(live=True)
            self.breakGlass("DEALER")
            self.saw("DEALER")
            self.cuff("DEALER")
            self.DEALERshootAI()


        def dontCheat():
            """the simple algorithm for the DEALER, it randomly guesses if it is live or blank and then plays accordingly"""
            def guessLive():
                self.drinkBeer()
                self.inverter()
                DEALERSmoke()
                self.cuff()
                self.saw()
                self.DEALERshootAI()

            def guessBlank():
                self.drinkBeer()
                self.inverter()
                DEALERSmoke()
                self.DEALERshootDEALER()

            if random.random() < 0.5: guessLive()
            else: guessBlank()

        if cantCheat: dontCheat()
        elif canCheat: normalCheat()
        else: superCheat()


"""


# Main game loop
def playGame():
    resetGame()
    while running:
        pass


# Play the game
playGame()
"""
