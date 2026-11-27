# ClashMaster

## Roadmap

### User Interfaces

#### Desktop Interface

Match setup  
Deck editor  
Human vs AI  
AI vs AI  
Replay viewer  

#### Operator Tools

Install check  
Demo data  
Release check  
Analysis scripts  

### Serving and Evaluation

#### Policy Service

JSON-lines protocol  
Episode lifecycle  
Warmup and act loop  
Recurrent state  

#### Evaluation

A vs B matches  
Alternating sides  
Match logs  
Summary metrics  
Win-rate reporting  

#### Offline Drivers

Offline agent  
Offline duel  

### Policy

#### Model

Observation tensorizer  
Entity encoder  
Relation encoder  
Spatial encoder  
LSTM memory  
Decision heads  
Value head  

#### Decoding

Action decoding  
Micro-action timing  
Multi-action window  
Action masks  

#### Checkpoints

Model config  
Catalog ids  
Training stage  
Compatibility checks  

### Learning

#### Imitation Learning

Replay screening  
Replay conversion  
IL cache  
Supervised training  

#### Reinforcement Learning

PPO  
League  
PFSP matchmaking  
Deck pool  
Reward shaping  
Distributed PPO  

#### Search

Forked engine search  
Candidate re-ranking  
Search labels  
Distillation  
Critic evaluation  

### Battle Environment

#### Contracts

Observation v1  
Action v1  
Episode config  
Catalog and attestation  

#### Runtime

FAIR observations  
Legal-action masks  
Execution timing  
Rewards and terminal  
Match factory  
Ruleset and attestation  

### Semantic Data

Competitive releases  
Card catalogs  
Mechanic catalogs  
Effect catalogs  
Projectile catalogs  
Runtime overlays  
Resource compiler  

### Engines

#### Libg.so Native

Probe  
Control protocol  
Host API  
Resident slots  

#### CRforge Simulation

JVM stand-in  
Vectorized battles  
Resident mode  
Native parity gate  

#### Live Official Client

Memory reader  
Frame parsers  
Observation adapter  
Live runner  

### Android Battle Anchor

#### Memory Reader

Device pre-flight  
Root / ADB root  
Process and libg base discovery  
Pointer chain and field offsets  
Player record reads  
Entity list and targeting  
Frame normalization  
Coherence check  

#### ADB Taps and Action

Screen calibration  
Hand-slot mapping  
Arena-cell targeting  
Tap batching  
Ability activation  

### Compute and Deployment

#### Android Guests

MuMu Player  
Linux AVD  
Emulator images  
Root access  

#### Engine Hosts

Single engine  
Windows engine cluster  
Resident slots  
ADB port forwarding  

#### Training Cluster

GPU hosts  
Learner rank  
Engine workers  
Topology and readiness  
Distributed launcher  

#### Builds and Releases

Offline APK build  
Probe build  
Probe install  
Version pinning  
Release checks  
CI tests  

### Artifacts and Data

Replay datasets  
IL cache shards  
Checkpoints  
Training replays  
Evaluation outputs  

### Tooling

Native analysis  
Build bring-up  
Live tools  
Dataset generators  
Performance research  
